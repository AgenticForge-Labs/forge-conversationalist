"""Bounded timing buffer for content-free generated speech features.

The caller must associate a generated stream with an observed output segment
before arming. This class has no audio sink, background task, MCP client or
robot connection. Track output is not measured acoustic speaker playout.
"""

from __future__ import annotations

import math
from collections import deque

_NATIVE_KEYS = frozenset({
    "schema", "stream_id", "sequence", "offset_ms", "duration_ms", "gain",
    "timing_source", "acoustic_playout_measured",
})
_ENVELOPE_KEYS = frozenset({
    "schema", "stream_id", "sequence", "offset_ms", "duration_ms", "active",
    "intensity", "rms_dbfs", "timing_source", "acoustic_playout_measured",
})


class NativeSpeechPacer:
    """Pace one explicitly associated output segment; never replay stale features.

    Both native gain and the existing generic speech envelope use the same
    ordered, bounded timing rules. The caller still has to establish the
    generated stream's identity at LiveKit output before arming either kind.
    """

    def __init__(self, *, max_hops: int = 1200, max_lateness_s: float = 0.15,
                 feature_kind: str = "native") -> None:
        if type(max_hops) is not int or not 1 <= max_hops <= 1200:
            raise ValueError("feature buffer must hold between 1 and 1200 hops")
        if (isinstance(max_lateness_s, bool) or not isinstance(max_lateness_s, (int, float))
                or not math.isfinite(max_lateness_s) or not 0 <= max_lateness_s <= 0.5):
            raise ValueError("feature lateness bound must be between 0 and 0.5 seconds")
        if feature_kind not in {"native", "envelope"}:
            raise ValueError("unsupported speech feature kind")
        self._feature_kind = feature_kind
        self._max_hops = max_hops
        self._max_lateness_s = float(max_lateness_s)
        self._stream: str | None = None
        self._next_sequence = 0
        self._anchor: float | None = None
        self._pending: deque[dict[str, object]] = deque()
        self._fenced = False

    @property
    def fenced(self) -> bool:
        return self._fenced

    def invalidate(self) -> None:
        """Permanently discard this segment after interruption or failure."""
        self._fenced = True
        self._pending.clear()
        self._anchor = None

    def bind(self, stream_id: str) -> None:
        """Reserve a generated stream before either features or playback arrive."""
        if (self._fenced or self._stream is not None or not isinstance(stream_id, str)
                or not 1 <= len(stream_id) <= 128):
            raise ValueError("one nonempty generated stream identity required")
        self._stream = stream_id

    def offer(self, frames: list[dict[str, object]]) -> bool:
        """Accept an ordered batch atomically; overflow fences the segment."""
        if self._fenced:
            return False
        if not isinstance(frames, list) or not 1 <= len(frames) <= 8:
            raise ValueError("one to eight speech features required")
        parsed = []
        stream = self._stream
        for index, frame in enumerate(frames):
            expected = _NATIVE_KEYS if self._feature_kind == "native" else _ENVELOPE_KEYS
            if not isinstance(frame, dict) or set(frame) != expected:
                raise ValueError("speech feature fields are invalid")
            name = frame["stream_id"]
            sequence = frame["sequence"]
            level = frame["gain"] if self._feature_kind == "native" else frame["intensity"]
            maximum = 1.5 if self._feature_kind == "native" else 1.0
            if (not isinstance(name, str) or not 1 <= len(name) <= 128
                    or (stream is not None and name != stream)
                    or type(sequence) is not int or sequence != self._next_sequence + index
                    or type(frame["offset_ms"]) is not int or frame["offset_ms"] != sequence * 50
                    or type(frame["duration_ms"]) is not int or frame["duration_ms"] != 50
                    or isinstance(level, bool) or not isinstance(level, (int, float))
                    or not math.isfinite(level) or not 0 <= level <= maximum
                    or frame["schema"] != (
                        "forge.native_speech_gain.v1" if self._feature_kind == "native"
                        else "forge.speech_envelope.v1"
                    )
                    or frame["timing_source"] != "generated_pcm"
                    or frame["acoustic_playout_measured"] is not False):
                raise ValueError("invalid, reordered, or mixed speech feature")
            if self._feature_kind == "envelope":
                rms = frame["rms_dbfs"]
                if (type(frame["active"]) is not bool or isinstance(rms, bool)
                        or not isinstance(rms, (int, float)) or not math.isfinite(rms)
                        or not -120 <= rms <= 0):
                    raise ValueError("invalid speech envelope feature")
            stream = name
            parsed.append(dict(frame))
        if len(self._pending) + len(parsed) > self._max_hops:
            self.invalidate()
            return False
        self._stream = stream
        self._next_sequence += len(parsed)
        self._pending.extend(parsed)
        return True

    def arm(self, *, stream_id: str, output_started_at: float) -> None:
        """Associate this stream with a caller-verified output segment clock."""
        if (self._fenced or self._anchor is not None or stream_id != self._stream
                or isinstance(output_started_at, bool)
                or not isinstance(output_started_at, (int, float))
                or not math.isfinite(output_started_at)):
            raise ValueError("valid, uniquely associated output start required")
        self._anchor = float(output_started_at)

    def take_due(self, *, now: float, max_batch: int = 8) -> list[dict[str, object]]:
        """Remove due hops once; late hops fence rather than burst or retry."""
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError("finite monotonic time required")
        if type(max_batch) is not int or not 1 <= max_batch <= 8:
            raise ValueError("batch size must be between 1 and 8")
        if self._fenced or self._anchor is None or not self._pending:
            return []
        deadline = self._anchor + int(self._pending[0]["offset_ms"]) / 1000.0
        if now - deadline > self._max_lateness_s:
            self.invalidate()
            return []
        due = []
        while self._pending and len(due) < max_batch:
            deadline = self._anchor + int(self._pending[0]["offset_ms"]) / 1000.0
            if deadline > now:
                break
            due.append(self._pending.popleft())
        return due
