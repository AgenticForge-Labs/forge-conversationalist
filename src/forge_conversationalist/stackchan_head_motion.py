"""Idle and speaking head motion for StackChan, ported from its own firmware.

Source of truth: the pinned M5Stack StackChan firmware modifiers
``main/stackchan/modifiers/idle_motion.h`` and ``speaking.h``. Numbers below
mirror them so the robot behaves like stock firmware, which is what the
daemon wobbler gives Reachy.

Units are the SDK's tenths of a degree. These functions only *choose* a
target; the provider still validates bounds and owns dispatch.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# idle_motion.h intervals.
IDLE_INTERVAL_MIN_MS = 4000
IDLE_INTERVAL_MAX_MS = 8000
# speaking.h intervals and amplitudes.
SPEAKING_MOTION_MIN_MS = 1500
SPEAKING_MOTION_MAX_MS = 2500
NOD_MIN, NOD_MAX = -20, 50
YAW_DRIFT = 40
PITCH_DRIFT = 20
# speaking.h mouth weights.
MOUTH_OPEN_MIN, MOUTH_OPEN_MAX = 40, 80
MOUTH_CLOSE_MIN, MOUTH_CLOSE_MAX = 0, 20


@dataclass(frozen=True)
class HeadTarget:
    yaw: int
    pitch: int
    speed: int


class StackChanHeadMotion:
    """Deterministic-when-seeded port of the firmware's head behaviour.

    ``next_idle`` reproduces IdleMotionModifier's four weighted actions;
    ``next_speaking`` reproduces SpeakingModifier's subtle nod/drift around
    the *current* angles rather than a fixed centre, so a large move is not
    snapped back.
    """

    def __init__(
        self,
        *,
        yaw_center: int = 0,
        pitch_center: int = 450,
        seed: int | None = None,
    ) -> None:
        self._rng = random.Random(seed)
        self._yaw = int(yaw_center)
        self._pitch = int(pitch_center)
        self._baseline: tuple[int, int] | None = None

    @property
    def current(self) -> tuple[int, int]:
        return (self._yaw, self._pitch)

    def _clamp(self, yaw: int, pitch: int) -> tuple[int, int]:
        # idle_motion.h uses the same envelope for its own random targets.
        return (max(-800, min(800, yaw)), max(0, min(600, pitch)))

    def next_idle(self) -> HeadTarget:
        """One weighted random idle action, exactly as the firmware picks it."""
        rng = self._rng
        action = rng.randint(0, 100)
        if action < 50:
            # Look around in normalized coordinates (-1..1) around centre.
            x = rng.uniform(-0.4, 0.4)
            y = rng.uniform(-0.95, 0.2)
            yaw = int(self._yaw + x * 800)
            pitch = int(self._pitch + y * 300)
            speed = rng.randint(150, 300)
        elif action < 80:
            yaw = self._yaw + rng.randint(-150, 150)
            pitch = self._pitch + rng.randint(-80, 80)
            speed = rng.randint(100, 250)
        elif action < 90:
            yaw = rng.randint(-500, 500)
            pitch = rng.randint(100, 400)
            speed = rng.randint(250, 400)
        else:
            yaw = 0
            pitch = rng.randint(50, 400)
            speed = rng.randint(100, 300)
        self._yaw, self._pitch = self._clamp(yaw, pitch)
        return HeadTarget(self._yaw, self._pitch, speed)

    def next_speaking(self) -> HeadTarget:
        """A small nod or sway around the last observed pose."""
        rng = self._rng
        if self._baseline is None:
            self._baseline = (self._yaw, self._pitch)
            target_yaw, target_pitch = self._yaw, self._pitch
        else:
            target_yaw, target_pitch = self._baseline
        speed = rng.randint(100, 200)
        if rng.randint(0, 9) < 5:
            target_pitch += rng.randint(NOD_MIN, NOD_MAX)
        else:
            target_yaw += rng.randint(-YAW_DRIFT, YAW_DRIFT)
            target_pitch += rng.randint(-PITCH_DRIFT, PITCH_DRIFT)
        yaw, pitch = self._clamp(target_yaw, target_pitch)
        # The baseline stays the observed pose, as in speaking.h: successive
        # subtle motions return toward it instead of drifting away.
        self._yaw, self._pitch = yaw, pitch
        return HeadTarget(yaw, pitch, speed)

    def observe(self, yaw: int, pitch: int, *, threshold: int = 300) -> None:
        """Sync the baseline after a large external move (speaking.h behaviour)."""
        if self._baseline is None or abs(yaw - self._baseline[0]) > threshold or abs(pitch - self._baseline[1]) > threshold:
            self._baseline = (yaw, pitch)
        self._yaw, self._pitch = yaw, pitch

    def mouth_weight(self, *, opening: bool) -> int:
        rng = self._rng
        if opening:
            return rng.randint(MOUTH_OPEN_MIN, MOUTH_OPEN_MAX)
        return rng.randint(MOUTH_CLOSE_MIN, MOUTH_CLOSE_MAX)
