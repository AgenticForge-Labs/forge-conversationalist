"""Content-free native speech amplitude extracted from outbound PCM only.

This is an offline analysis primitive. It neither opens a media device nor
schedules or commands robot motion. The timestamps are generated PCM offsets.
"""

from __future__ import annotations

from dataclasses import dataclass

from .reachy_speech_tapper import HOP_MS, SpeechTapper


@dataclass(frozen=True, slots=True)
class NativeSpeechFeature:
    sequence: int
    gain: float

    def as_wire(self, *, stream_id: str) -> dict[str, object]:
        if not stream_id or len(stream_id) > 128:
            raise ValueError("stream_id must be a nonempty bounded identifier")
        return {
            "schema": "forge.native_speech_gain.v1",
            "stream_id": stream_id,
            "sequence": self.sequence,
            "offset_ms": self.sequence * HOP_MS,
            "duration_ms": HOP_MS,
            "gain": self.gain,
            "timing_source": "generated_pcm",
            "acoustic_playout_measured": False,
        }


class NativeSpeechFeatureAnalyzer:
    """Incrementally reproduce the pinned SDK's pre-oscillator gain."""

    def __init__(self, sample_rate: int, channels: int = 1) -> None:
        if type(sample_rate) is not int or not 8000 <= sample_rate <= 96000:
            raise ValueError("sample rate must be an integer in [8000, 96000]")
        if type(channels) is not int or not 1 <= channels <= 2:
            raise ValueError("channels must be mono or stereo")
        self.sample_rate = sample_rate
        self.channels = channels
        self._tapper = SpeechTapper(sample_rate=sample_rate)
        self._sequence = 0

    def feed_frame(self, frame: object) -> list[NativeSpeechFeature]:
        if (getattr(frame, "sample_rate", None) != self.sample_rate
                or getattr(frame, "num_channels", None) != self.channels):
            raise ValueError("audio frame format changed within synthesized stream")
        return self.feed_pcm16(getattr(frame, "data", None))

    def feed_pcm16(self, data: object) -> list[NativeSpeechFeature]:
        try:
            view = memoryview(data)
            samples = view if view.format == "h" and view.ndim == 1 else view.cast("B").cast("h")
        except (TypeError, ValueError) as exc:
            raise TypeError("signed 16-bit PCM buffer required") from exc
        if len(samples) % self.channels:
            raise ValueError("PCM buffer is not channel-aligned")
        if self.channels == 1:
            mono = [float(sample) / 32768.0 for sample in samples]
        else:
            mono = [(float(samples[i]) + float(samples[i + 1])) / 65536.0
                    for i in range(0, len(samples), 2)]
        result = []
        for _offsets, gain in self._tapper.feed_with_gain(mono):
            result.append(NativeSpeechFeature(sequence=self._sequence, gain=gain))
            self._sequence += 1
        return result
