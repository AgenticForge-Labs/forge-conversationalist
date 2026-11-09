"""Optional binding to an output's verified segment-start capability.

No audio sink or playback service is created. A stock LiveKit output does not
provide this capability, so attachment returns false and motion remains off.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Protocol

SEGMENT_ID_KEY = "lk.playout_segment_id"


class _Ensemble(Protocol):
    def begin_native_gain(self, stream_id: str) -> bool: ...
    def offer_native_gain(self, frames: list[dict[str, object]]) -> bool: ...
    def cancel_native_gain(self, stream_id: str) -> None: ...
    def associate_native_playback(self, stream_id: str, *, output_started_at: float) -> bool: ...


def tag_audio_frame(frame: object, stream_id: str) -> bool:
    """Add only an opaque segment identity to a compatible outgoing frame."""
    userdata = getattr(frame, "userdata", None)
    if (not isinstance(stream_id, str) or not 1 <= len(stream_id) <= 128
            or not isinstance(userdata, dict)):
        return False
    existing = userdata.get(SEGMENT_ID_KEY)
    if existing is not None and existing != stream_id:
        return False
    userdata[SEGMENT_ID_KEY] = stream_id
    return True


class IdentifiedPlaybackBinding:
    """Associate content-free TTS features with output-reported segment starts."""

    def __init__(self, associate: Callable[..., bool]) -> None:
        if not callable(associate):
            raise TypeError("segment association callback required")
        self._associate = associate
        self._output = None

    def attach(self, output: object) -> bool:
        if self._output is not None:
            raise RuntimeError("identified playback is already attached")
        on = getattr(output, "on", None)
        off = getattr(output, "off", None)
        if (getattr(output, "identified_playback_supported", False) is not True
                or not callable(on) or not callable(off)):
            return False
        on("identified_playback_started", self._on_started)
        self._output = output
        return True

    def detach(self) -> None:
        if self._output is not None:
            self._output.off("identified_playback_started", self._on_started)
            self._output = None

    def _on_started(self, event: object) -> None:
        if self._output is None:
            return
        stream_id = getattr(event, "segment_id", None)
        ns = getattr(event, "monotonic_ns", None)
        if (not isinstance(stream_id, str) or not 1 <= len(stream_id) <= 128
                or type(ns) is not int or not 0 <= ns <= 2**63 - 1):
            return
        start = ns / 1_000_000_000
        if math.isfinite(start):
            self._associate(stream_id, output_started_at=start)


class IdentifiedSpeechBridge:
    """Connect tagged TTS features to one existing room-owned ensemble."""

    def __init__(self, ensemble: _Ensemble) -> None:
        self._ensemble = ensemble
        self._binding = IdentifiedPlaybackBinding(ensemble.associate_native_playback)
        self._attached = False
        self._streams: set[str] = set()

    def attach(self, output: object) -> bool:
        self._attached = self._binding.attach(output)
        return self._attached

    def detach(self) -> None:
        self._binding.detach()
        self._attached = False
        for stream in self._streams:
            self._ensemble.cancel_native_gain(stream)
        self._streams.clear()

    def begin(self, stream_id: str) -> bool:
        """Reserve a stream before its first output frame can start playing."""
        if not self._attached or not self._ensemble.begin_native_gain(stream_id):
            return False
        self._streams.add(stream_id)
        return True

    def offer(self, frames: list[dict[str, object]]) -> bool:
        if (not self._attached or not isinstance(frames, list) or not frames
                or not isinstance(frames[0], dict)):
            return False
        stream = frames[0].get("stream_id")
        if not isinstance(stream, str):
            return False
        if stream not in self._streams:
            return False
        return self._ensemble.offer_native_gain(frames)

    def abort(self, stream_id: str) -> None:
        self._streams.discard(stream_id)
        self._ensemble.cancel_native_gain(stream_id)
