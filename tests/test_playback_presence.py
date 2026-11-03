"""Software-only playback/semantic arbitration; does not open LiveKit or robots."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from forge_conversationalist.playback_presence import PlaybackPresence
from forge_conversationalist.text_hook import ConversationStateChanged


class AudioOutput:
    def __init__(self):
        self.listeners = {}

    def on(self, name, callback):
        self.listeners.setdefault(name, []).append(callback)

    def off(self, name, callback):
        self.listeners[name].remove(callback)

    def emit(self, name, **kwargs):
        for handler in list(self.listeners.get(name, [])):
            handler(SimpleNamespace(**kwargs))


def signal(state, source="agent", interrupted=False):
    return ConversationStateChanged(state=state, source=source, interrupted=interrupted)


def test_without_playback_output_preserves_existing_semantic_state_mode():
    events = []
    observer = PlaybackPresence(lambda *args: events.append(args))
    assert observer.attach(None) is False
    observer.observe(signal("listening"))
    observer.observe(signal("speaking"))
    observer.observe(signal("idle"))
    assert events == [
        ("listening", "agent_state", False),
        ("speaking", "agent_state", False),
        ("idle", "agent_state", False),
    ]


def test_playback_events_gate_speaking_and_ignore_early_idle():
    events = []
    audio = AudioOutput()
    observer = PlaybackPresence(lambda *args: events.append(args))
    assert observer.attach(audio)
    observer.observe(signal("listening"))
    observer.observe(signal("speaking"))  # generation != output playout
    assert events == [("listening", "agent_state", False)]

    audio.emit("playback_started", created_at=123.0)
    observer.observe(signal("idle"))  # do not shut speaking off before output drains
    audio.emit("playback_finished", playback_position=2.0, interrupted=False)
    assert events == [
        ("listening", "agent_state", False),
        ("speaking", "playback", False),
        ("idle", "playback", False),
    ]
    # Duplicate finish cannot send new robot motion or duplicate interruption.
    audio.emit("playback_finished", playback_position=2.0, interrupted=True)
    assert len(events) == 3
    observer.detach()
    audio.emit("playback_started", created_at=124.0)
    assert len(events) == 3


def test_playback_finished_restores_listening_if_semantic_speaking_is_stale():
    events = []
    audio = AudioOutput()
    observer = PlaybackPresence(lambda *args: events.append(args))
    observer.attach(audio)
    observer.observe(signal("speaking"))
    audio.emit("playback_started", created_at=124.0)
    audio.emit("playback_finished", playback_position=0.6, interrupted=False)
    assert events == [
        ("listening", "agent_state", False),
        ("speaking", "playback", False),
        ("listening", "playback", False),
    ]


def test_barge_in_is_urgent_and_stale_playback_callbacks_cannot_rearm():
    events = []
    audio = AudioOutput()
    observer = PlaybackPresence(lambda *args: events.append(args))
    observer.attach(audio)
    observer.observe(signal("speaking"))
    audio.emit("playback_started", created_at=1.0)
    observer.observe(signal("listening", "user", interrupted=True))
    audio.emit("playback_finished", playback_position=0.2, interrupted=True)
    audio.emit("playback_started", created_at=2.0)  # stale late-start
    assert events == [
        ("listening", "agent_state", False),
        ("speaking", "playback", False),
        ("listening", "agent_state", True),
    ]
    # A NEW semantic agent-speaking transition permits a new output interval.
    observer.observe(signal("speaking", "agent"))
    audio.emit("playback_started", created_at=3.0)
    assert events[-1] == ("speaking", "playback", False)


def test_interrupted_playback_reports_explicit_interrupt_not_physical_stop():
    events = []
    audio = AudioOutput()
    observer = PlaybackPresence(lambda *args: events.append(args))
    observer.attach(audio)
    audio.emit("playback_started", created_at=1.0)
    audio.emit("playback_finished", playback_position=0.1, interrupted=True)
    assert events == [
        ("speaking", "playback", False),
        ("idle", "playback", True),
    ]


def test_unavailable_audio_output_does_not_disable_existing_conversation():
    events = []
    observer = PlaybackPresence(lambda *args: events.append(args))
    assert observer.attach(object()) is False
    observer.observe(signal("speaking"))
    assert events == [("speaking", "agent_state", False)]
    with pytest.raises(TypeError):
        PlaybackPresence(None)
