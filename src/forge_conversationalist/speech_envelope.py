"""Device-neutral analysis of outgoing signed 16-bit PCM.

Inspired by the envelope/VAD approach in Pollen's pinned Reachy Mini
speech_tapper.py, not by its robot-specific oscillator or motion scheduler.
The output is *generated-audio* timing: it is NOT speaker playout or motion
evidence. No PCM or words are persisted or transmitted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

HOP_MS = 50
FRAME_MS = 20
VAD_ON_DBFS = -35.0
VAD_OFF_DBFS = -45.0
VAD_RELEASE_HOPS = 5
ENV_FOLLOW_GAIN = 0.65
LOUDNESS_LOW_DBFS = -46.0
LOUDNESS_HIGH_DBFS = -18.0
LOUDNESS_OFFSET_DB = 4.0
LOUDNESS_GAMMA = 0.9


@dataclass(frozen=True, slots=True)
class SpeechEnvelopeHop:
    """A bounded, nonsemantic feature sampled on the outgoing audio timeline."""

    sequence: int
    offset_ms: int
    duration_ms: int
    active: bool
    intensity: float
    rms_dbfs: float

    def as_wire(self, *, stream_id: str) -> dict[str, object]:
        """One content-free hop for a future versioned transport contract."""
        if not stream_id or len(stream_id) > 128:
            raise ValueError("stream_id must be a nonempty bounded identifier")
        return {
            "schema": "forge.speech_envelope.v1",
            "stream_id": stream_id,
            "sequence": self.sequence,
            "offset_ms": self.offset_ms,
            "duration_ms": self.duration_ms,
            "active": self.active,
            "intensity": self.intensity,
            "rms_dbfs": self.rms_dbfs,
            "timing_source": "generated_pcm",
            "acoustic_playout_measured": False,
        }


class SpeechEnvelopeAnalyzer:
    """Incremental, deterministic PCM envelope with bounded 50 ms windows.

    Only outbound synthesized PCM may enter this analyzer; do not use the
    microphone/STT path. The analyzer retains at most one hop of mono samples
    plus a rolling 20 ms frame. Reset for each independent synthesized stream.
    Movement curves, device choice and physical limits belong to Puppeteer.
    """

    def __init__(self, sample_rate: int, channels: int = 1) -> None:
        if type(sample_rate) is not int or not 8000 <= sample_rate <= 96000:
            raise ValueError("sample rate must be an integer in [8000, 96000]")
        if type(channels) is not int or not 1 <= channels <= 2:
            raise ValueError("channels must be mono or stereo")
        self.sample_rate = sample_rate
        self.channels = channels
        self._hop_samples = sample_rate * HOP_MS // 1000
        self._frame_samples = sample_rate * FRAME_MS // 1000
        self.reset()

    def reset(self) -> None:
        self._carry: list[float] = []
        self._recent: list[float] = []
        self._sequence = 0
        self._vad_on = False
        self._vad_below = 0
        self._envelope = 0.0

    def feed_frame(self, frame: object) -> list[SpeechEnvelopeHop]:
        """Read a LiveKit-compatible AudioFrame; never retain the original data."""
        rate = getattr(frame, "sample_rate", None)
        channels = getattr(frame, "num_channels", None)
        if rate != self.sample_rate or channels != self.channels:
            raise ValueError("audio frame format changed within synthesized stream")
        data = getattr(frame, "data", None)
        if data is None:
            raise TypeError("audio frame must expose signed 16-bit PCM data")
        return self.feed_pcm16(data)

    def feed_pcm16(self, data: object) -> list[SpeechEnvelopeHop]:
        """Accept interleaved host-native signed int16 samples (LiveKit format)."""
        try:
            view = memoryview(data)
            samples = view if view.format == "h" and view.ndim == 1 else (
                view.cast("B").cast("h")
            )
        except (TypeError, ValueError) as exc:
            raise TypeError("signed 16-bit PCM buffer required") from exc
        if len(samples) % self.channels:
            raise ValueError("PCM buffer is not channel-aligned")
        if self.channels == 1:
            self._carry.extend(float(sample) / 32768.0 for sample in samples)
        else:
            self._carry.extend(
                (float(samples[i]) + float(samples[i + 1])) / 65536.0
                for i in range(0, len(samples), 2)
            )
        result: list[SpeechEnvelopeHop] = []
        while len(self._carry) >= self._hop_samples:
            chunk = self._carry[:self._hop_samples]
            del self._carry[:self._hop_samples]
            self._recent = (self._recent + chunk)[-self._frame_samples:]
            rms = math.sqrt(sum(sample * sample for sample in self._recent)
                            / len(self._recent) + 1e-12)
            db = 20.0 * math.log10(rms)
            if db >= VAD_ON_DBFS:
                self._vad_on = True
                self._vad_below = 0
            elif db <= VAD_OFF_DBFS:
                self._vad_below += 1
                if self._vad_below >= VAD_RELEASE_HOPS:
                    self._vad_on = False
            target = 1.0 if self._vad_on else 0.0
            self._envelope += ENV_FOLLOW_GAIN * (target - self._envelope)
            normalized = (db + LOUDNESS_OFFSET_DB - LOUDNESS_LOW_DBFS) / (
                LOUDNESS_HIGH_DBFS - LOUDNESS_LOW_DBFS
            )
            loudness = max(0.0, min(1.0, normalized)) ** LOUDNESS_GAMMA
            intensity = max(0.0, min(1.0, loudness * self._envelope))
            result.append(SpeechEnvelopeHop(
                sequence=self._sequence,
                offset_ms=self._sequence * HOP_MS,
                duration_ms=HOP_MS,
                active=self._vad_on,
                intensity=intensity,
                rms_dbfs=max(-120.0, min(0.0, db)),
            ))
            self._sequence += 1
        return result
