from types import SimpleNamespace

import pytest

from forge_conversationalist.identified_playback import (
    SEGMENT_ID_KEY,
    IdentifiedPlaybackBinding,
    tag_audio_frame,
)


class Output:
    identified_playback_supported = True

    def __init__(self):
        self.listeners = {}

    def on(self, event, callback):
        self.listeners[event] = callback

    def off(self, event, callback):
        assert self.listeners.pop(event) == callback

    def emit(self, event):
        self.listeners["identified_playback_started"](event)


def test_tag_preserves_audio_and_does_not_overwrite_other_identity():
    frame = SimpleNamespace(data=b"unchanged", userdata={"existing": "value"})
    assert tag_audio_frame(frame, "tts-1")
    assert frame.data == b"unchanged"
    assert frame.userdata == {"existing": "value", SEGMENT_ID_KEY: "tts-1"}
    assert not tag_audio_frame(frame, "tts-2")
    assert frame.userdata[SEGMENT_ID_KEY] == "tts-1"
    assert not tag_audio_frame(SimpleNamespace(), "tts-1")


def test_only_capable_output_can_arm_identified_segment():
    calls = []
    binding = IdentifiedPlaybackBinding(lambda stream, **kw: calls.append((stream, kw)))
    assert not binding.attach(SimpleNamespace())
    output = Output()
    assert binding.attach(output)
    output.emit(SimpleNamespace(segment_id="tts-1", monotonic_ns=4_250_000_000))
    assert calls == [("tts-1", {"output_started_at": 4.25})]
    with pytest.raises(RuntimeError):
        binding.attach(output)
    binding.detach()
    assert not output.listeners


def test_malformed_or_unattributed_events_never_arm():
    calls = []
    binding = IdentifiedPlaybackBinding(lambda *args, **kw: calls.append((args, kw)))
    output = Output()
    assert binding.attach(output)
    for event in (SimpleNamespace(), SimpleNamespace(segment_id="", monotonic_ns=1),
                  SimpleNamespace(segment_id="tts", monotonic_ns=True),
                  SimpleNamespace(segment_id="tts", monotonic_ns=-1)):
        output.emit(event)
    assert not calls
