"""Backend assistant text callback, independent of room clients and audio."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from forge_conversationalist.text_hook import (
    AssistantTextHook,
    SessionStateHook,
    install_assistant_text_hook,
    install_session_state_hook,
    remove_assistant_text_hook,
    remove_session_state_hook,
)


@dataclass
class Message:
    id: str
    role: str
    text_content: str | None


def event(item: object) -> SimpleNamespace:
    return SimpleNamespace(item=item)


def test_hook_delivers_text_with_content_free_completion_signal() -> None:
    returned = []
    hook = AssistantTextHook(lambda signal, text: returned.append((signal, text)))

    hook(event(Message("item-1", "assistant", "  Hello there.  ")))

    assert len(returned) == 1
    signal, text = returned[0]
    assert signal.item_id
    assert text == "Hello there."
    assert signal.interrupted is False
    assert not hasattr(signal, "text")
    assert not hasattr(signal, "cue")


def test_hook_preserves_livekit_interrupted_flag() -> None:
    returned = []
    hook = AssistantTextHook(lambda signal, text: returned.append((signal, text)))
    hook(event(SimpleNamespace(
        id="interrupted-1", role="assistant", text_content="partial answer", interrupted=True
    )))

    assert returned[0][0].interrupted is True
    assert returned[0][1] == "partial answer"


@pytest.mark.parametrize(
    "message",
    [
        Message("user-1", "user", "User text"),
        Message("empty-1", "assistant", " \n "),
        Message("no-text", "assistant", None),
        SimpleNamespace(id="handoff-1", role=None, text_content=""),
    ],
)
def test_hook_ignores_user_empty_and_non_message_items(message: object) -> None:
    returned = []
    AssistantTextHook(lambda signal, text: returned.append((signal, text)))(event(message))
    assert returned == []


def test_hook_suppresses_duplicate_ids_but_accepts_distinct_text_items() -> None:
    returned = []
    hook = AssistantTextHook(lambda signal, text: returned.append((signal, text)))
    first = event(Message("same-id", "assistant", "first"))
    hook(first)
    hook(event(Message("same-id", "assistant", "duplicate delivery")))
    hook(event(Message("next-id", "assistant", "second")))

    assert [text for signal, text in returned] == ["first", "second"]


def test_dedupe_cache_is_bounded() -> None:
    returned = []
    hook = AssistantTextHook(lambda signal, text: returned.append((signal, text)), max_seen=1)
    first = event(Message("first", "assistant", "first"))
    hook(first)
    hook(event(Message("second", "assistant", "second")))
    hook(first)
    assert [text for signal, text in returned] == ["first", "second", "first"]


def test_hook_wires_to_livekit_event_and_can_be_removed() -> None:
    class Session:
        def __init__(self) -> None:
            self.listeners = {}

        def on(self, name, callback):
            self.listeners.setdefault(name, []).append(callback)

        def off(self, name, callback):
            self.listeners[name].remove(callback)

        def emit(self, name, value):
            for callback in list(self.listeners.get(name, [])):
                callback(value)

    session = Session()
    returned = []
    hook = install_assistant_text_hook(
        session, lambda signal, text: returned.append((signal, text))
    )
    session.emit("conversation_item_added", event(Message("live-1", "assistant", "reply")))
    assert [text for signal, text in returned] == ["reply"]

    remove_assistant_text_hook(session, hook)
    session.emit("conversation_item_added", event(Message("live-2", "assistant", "ignored")))
    assert [text for signal, text in returned] == ["reply"]


def test_invalid_hook_limits_and_session_fail_closed() -> None:
    with pytest.raises(ValueError):
        AssistantTextHook(lambda _: None, max_seen=0)
    with pytest.raises(TypeError):
        install_assistant_text_hook(object(), lambda _: None)


def test_livekit_state_events_map_and_dedupe_generic_presence() -> None:
    states = []
    hook = SessionStateHook(states.append)
    hook.on_agent_state_changed(SimpleNamespace(new_state="initializing"))
    hook.on_agent_state_changed(SimpleNamespace(new_state="listening"))
    hook.on_agent_state_changed(SimpleNamespace(new_state="speaking"))
    # Routine user listening cannot override an active agent speaking state.
    hook.on_user_state_changed(SimpleNamespace(new_state="listening"))
    # User speech does represent an interruption and maps to listening.
    hook.on_user_state_changed(SimpleNamespace(new_state="speaking"))
    # If user speech ends while the agent remains speaking, reflect that state.
    hook.on_user_state_changed(SimpleNamespace(new_state="listening"))
    hook.on_agent_state_changed(SimpleNamespace(new_state="idle"))

    assert [(item.state, item.source) for item in states] == [
        ("listening", "agent"),
        ("speaking", "agent"),
        ("listening", "user"),
        ("speaking", "user"),
        ("idle", "agent"),
    ]


def test_state_hook_registers_and_removes_both_livekit_events() -> None:
    class Session:
        def __init__(self) -> None:
            self.listeners = {}

        def on(self, name, callback):
            self.listeners.setdefault(name, []).append(callback)

        def off(self, name, callback):
            self.listeners[name].remove(callback)

        def emit(self, name, value):
            for callback in list(self.listeners.get(name, [])):
                callback(value)

    session = Session()
    states = []
    hook = install_session_state_hook(session, states.append)
    session.emit("agent_state_changed", SimpleNamespace(new_state="speaking"))
    session.emit("user_state_changed", SimpleNamespace(new_state="listening"))
    session.emit("user_state_changed", SimpleNamespace(new_state="speaking"))
    assert [state.state for state in states] == ["speaking", "listening"]

    remove_session_state_hook(session, hook)
    session.emit("agent_state_changed", SimpleNamespace(new_state="idle"))
    assert [state.state for state in states] == ["speaking", "listening"]
