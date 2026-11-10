"""Plain-Python port of the pinned Reachy speech tapper and idle drift.

Source of truth: the pinned Reachy SDK's ``reachy_mini.motion.speech_tapper``
(``SwayRollRT``), which the stock audio wobbler uses. Numbers here mirror
that module so the same speech
produces the same per-hop offsets, without importing GStreamer. Scheduling,
target composition, and physical motion are separate concerns.

Units follow the daemon's ``SetSpeechOffsetsCmd``:
``(x_m, y_m, z_m, roll_rad, pitch_rad, yaw_rad)``.
"""

from __future__ import annotations

import math
import random

import numpy as np

FRAME_MS = 20
HOP_MS = 50

SWAY_MASTER = 1.5
SENS_DB_OFFSET = +4.0
VAD_DB_ON = -35.0
VAD_DB_OFF = -45.0
VAD_ATTACK_MS = 40
VAD_RELEASE_MS = 250
ENV_FOLLOW_GAIN = 0.65

SWAY_F_PITCH = 2.2
SWAY_A_PITCH_DEG = 4.5
SWAY_F_YAW = 0.6
SWAY_A_YAW_DEG = 7.5
SWAY_F_ROLL = 1.3
SWAY_A_ROLL_DEG = 2.25
SWAY_F_X = 0.35
SWAY_A_X_MM = 4.5
SWAY_F_Y = 0.45
SWAY_A_Y_MM = 3.75
SWAY_F_Z = 0.25
SWAY_A_Z_MM = 2.25

SWAY_DB_LOW = -46.0
SWAY_DB_HIGH = -18.0
LOUDNESS_GAMMA = 0.9
SWAY_ATTACK_MS = 50
SWAY_RELEASE_MS = 250

_ATTACK_FR = max(1, int(VAD_ATTACK_MS / HOP_MS))
_RELEASE_FR = max(1, int(VAD_RELEASE_MS / HOP_MS))
_SWAY_ATTACK_FR = max(1, int(SWAY_ATTACK_MS / HOP_MS))
_SWAY_RELEASE_FR = max(1, int(SWAY_RELEASE_MS / HOP_MS))


def _loudness_gain(db: float) -> float:
    t = (db + SENS_DB_OFFSET - SWAY_DB_LOW) / (SWAY_DB_HIGH - SWAY_DB_LOW)
    t = 0.0 if t < 0.0 else (min(t, 1.0))
    return t**LOUDNESS_GAMMA


class SpeechTapper:
    """Per-hop 6-DOF head offsets from one synthesized speech stream.

    ``feed`` accepts already-normalized float samples in [-1, 1]; the caller
    converts PCM. Output is bounded by the constants above, so no target can
    exceed what the stock app produces.
    """

    def __init__(self, *, sample_rate: int = 24000, seed: int = 7) -> None:
        self.sample_rate = int(sample_rate)
        self.frame = int(self.sample_rate * FRAME_MS / 1000)
        self.hop = int(self.sample_rate * HOP_MS / 1000)
        rng = np.random.default_rng(seed)
        tau = 2 * math.pi
        self._phases = [rng.random() * tau for _ in range(6)]
        self.reset()

    def reset(self) -> None:
        self._tail: list[float] = []
        self._vad_on = False
        self._above = 0
        self._below = 0
        self._env = 0.0
        self._up = 0
        self._down = 0
        self._last_gain = 0.0
        self.t = 0.0

    def feed(self, samples: list[float]) -> list[tuple[float, float, float, float, float, float]]:
        """Consume samples, returning one bounded offset tuple per 50 ms hop."""
        return [offsets for offsets, _gain in self.feed_with_gain(samples)]

    def feed_with_gain(
        self, samples: list[float],
    ) -> list[tuple[tuple[float, float, float, float, float, float], float]]:
        """Return native offsets and their content-free scalar amplitude per hop.

        The scalar is the SDK's loudness and envelope product, before any
        axis mapping. It can be transported without sending motor targets.
        """
        if not samples:
            return []
        buf = self._tail + samples
        out = []
        while len(buf) >= self.hop:
            hop = buf[: self.hop]
            buf = buf[self.hop:]
            offsets = self._hop(hop)
            out.append((offsets, self._last_gain))
        self._tail = buf
        return out

    def _hop(self, hop: list[float]):
        # SwayRollRT keeps the most recent 20 ms of each 50 ms hop and
        # computes RMS using float32 arithmetic.
        frame = np.asarray(hop[-self.frame:], dtype=np.float32)
        rms = np.sqrt(np.mean(frame * frame, dtype=np.float32) + 1e-12, dtype=np.float32)
        db = 20.0 * math.log10(float(rms) + 1e-12)

        if db >= VAD_DB_ON:
            self._above += 1
            self._below = 0
            if not self._vad_on and self._above >= _ATTACK_FR:
                self._vad_on = True
        elif db <= VAD_DB_OFF:
            self._below += 1
            self._above = 0
            if self._vad_on and self._below >= _RELEASE_FR:
                self._vad_on = False

        if self._vad_on:
            self._up = min(_SWAY_ATTACK_FR, self._up + 1)
            self._down = 0
        else:
            self._down = min(_SWAY_RELEASE_FR, self._down + 1)
            self._up = 0

        target = (
            self._up / _SWAY_ATTACK_FR if self._vad_on
            else 1.0 - (self._down / _SWAY_RELEASE_FR)
        )
        self._env += ENV_FOLLOW_GAIN * (target - self._env)
        self._env = min(1.0, max(0.0, self._env))

        loud = _loudness_gain(db) * SWAY_MASTER
        env = self._env
        self._last_gain = loud * env
        self.t += HOP_MS / 1000.0
        tau = 2 * math.pi
        p_pitch, p_yaw, p_roll, p_x, p_y, p_z = self._phases

        pitch = math.radians(SWAY_A_PITCH_DEG) * loud * env * math.sin(tau * SWAY_F_PITCH * self.t + p_pitch)
        yaw = math.radians(SWAY_A_YAW_DEG) * loud * env * math.sin(tau * SWAY_F_YAW * self.t + p_yaw)
        roll = math.radians(SWAY_A_ROLL_DEG) * loud * env * math.sin(tau * SWAY_F_ROLL * self.t + p_roll)
        x_mm = SWAY_A_X_MM * loud * env * math.sin(tau * SWAY_F_X * self.t + p_x)
        y_mm = SWAY_A_Y_MM * loud * env * math.sin(tau * SWAY_F_Y * self.t + p_y)
        z_mm = SWAY_A_Z_MM * loud * env * math.sin(tau * SWAY_F_Z * self.t + p_z)
        return (x_mm / 1000.0, y_mm / 1000.0, z_mm / 1000.0, roll, pitch, yaw)


class IdleDrift:
    """Slow 6-DOF rest drift, in the same units as the tapper.

    The stock app relies on daemon breathing for idle; this is a deliberate
    small motion so an idle room is not frozen. Amplitudes are far below the
    speech tapper's so idle never competes with speech.
    """

    def __init__(self, *, period_s: float = 7.5, seed: int = 3) -> None:
        rng = random.Random(seed)
        self._period = period_s
        self._phase = rng.random() * 2 * math.pi

    def at(self, t: float) -> tuple[float, float, float, float, float, float]:
        tau = 2 * math.pi
        w = tau / self._period
        pitch = math.radians(1.6) * math.sin(w * t + self._phase)
        yaw = math.radians(1.2) * math.sin(w * 0.41 * t + self._phase * 1.7)
        roll = math.radians(0.9) * math.sin(w * 0.63 * t + self._phase * 0.6)
        z_m = 0.0012 * math.sin(w * 0.77 * t)
        return (0.0, 0.0, z_m, roll, pitch, yaw)
