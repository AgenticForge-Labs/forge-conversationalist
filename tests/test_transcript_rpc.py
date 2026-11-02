"""Read-only transcript RPC boundaries and size limits."""

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from forge_conversationalist.transcript_rpc import (
    MAX_TRANSCRIPT_MESSAGES,
    MAX_TRANSCRIPT_RESPONSE_BYTES,
    MAX_TRANSCRIPT_TEXT_CHARS,
    register_transcript_rpc,
    transcript_messages,
    transcript_payload,
)


@dataclass
class Message:
    id: str
    role: str
    text_content: str | None


class History:
    def __init__(self, items):
        self.items = items
        self.read_count = 0

    def messages(self):
        self.read_count += 1
        return self.items


def test_transcript_filters_hidden_and_empty_items_and_keeps_ids() -> None:
    history = History([
        Message("s1", "system", "hidden instructions"),
        Message("d1", "developer", "hidden prompt"),
        Message("u1", "user", " Hello "),
        Message("tool1", "tool", "hidden tool result"),
        Message("a-empty", "assistant", "  "),
        Message("a1", "assistant", " Hi there "),
    ])

    assert transcript_messages(history) == [
        {"id": "u1", "role": "user", "text": "Hello"},
        {"id": "a1", "role": "assistant", "text": "Hi there"},
    ]


def test_transcript_bounds_count_text_and_serialized_payload() -> None:
    history = History([
        Message(f"m-{index}", "user" if index % 2 == 0 else "assistant", "🌊" * 5_000)
        for index in range(MAX_TRANSCRIPT_MESSAGES + 10)
    ])

    items = transcript_messages(history)
    payload = transcript_payload(history)
    decoded = json.loads(payload)
    assert len(items) <= MAX_TRANSCRIPT_MESSAGES
    assert items[-1]["id"] == f"m-{MAX_TRANSCRIPT_MESSAGES + 9}"
    assert all(len(item["text"]) <= MAX_TRANSCRIPT_TEXT_CHARS for item in items)
    assert len(payload.encode("utf-8")) <= MAX_TRANSCRIPT_RESPONSE_BYTES
    assert decoded["messages"] == items


def test_transcript_requires_message_ids_and_chat_context_api() -> None:
    assert transcript_messages(History([SimpleNamespace(role="assistant", text_content="Hi")])) == []
    with pytest.raises(TypeError):
        transcript_messages(object())


def test_rpc_returns_json_only_for_exact_authorized_caller() -> None:
    class Participant:
        def register_rpc_method(self, method, callback):
            self.method = method
            self.callback = callback

    history = History([Message("u1", "user", "question"), Message("a1", "assistant", "answer")])
    participant = Participant()
    register_transcript_rpc(
        participant,
        history=history,
        authorized_identity="person-authorized",
        rpc_error_factory=lambda message: PermissionError(message),
    )

    assert participant.method == "forge.get_transcript"
    before = history.read_count
    with pytest.raises(PermissionError):
        asyncio.run(participant.callback(SimpleNamespace(caller_identity="person-other", payload="{}")))
    assert history.read_count == before  # Check authorization before reading transcript data.

    response = asyncio.run(participant.callback(
        SimpleNamespace(caller_identity="person-authorized", payload="")
    ))
    assert json.loads(response) == {
        "messages": [
            {"id": "u1", "role": "user", "text": "question"},
            {"id": "a1", "role": "assistant", "text": "answer"},
        ]
    }


def test_rpc_waits_for_authorized_participant_before_reading_history() -> None:
    class Participant:
        def register_rpc_method(self, method, callback):
            self.method = method
            self.callback = callback

    class Room:
        def __init__(self):
            self.remote_participants = {}
            self.listeners = []

        def on(self, event_name, callback):
            assert event_name == "participant_connected"
            self.listeners.append(callback)

        def off(self, event_name, callback):
            assert event_name == "participant_connected"
            self.listeners.remove(callback)

        def join(self, identity):
            participant = SimpleNamespace(identity=identity)
            self.remote_participants[identity] = participant
            for callback in list(self.listeners):
                callback(participant)

    async def scenario():
        room = Room()
        history = History([Message("a1", "assistant", "answer")])
        participant = Participant()
        register_transcript_rpc(
            participant,
            history=history,
            authorized_identity="person-authorized",
            room=room,
            participant_wait_timeout=0.2,
            rpc_error_factory=lambda message: TimeoutError(message),
        )
        request = SimpleNamespace(caller_identity="person-authorized", payload="{}")
        task = asyncio.create_task(participant.callback(request))
        await asyncio.sleep(0.01)
        assert history.read_count == 0
        room.join("person-other")
        await asyncio.sleep(0.01)
        assert not task.done()
        room.join("person-authorized")
        response = await task
        assert json.loads(response)["messages"][0]["text"] == "answer"
        assert history.read_count == 1
        assert room.listeners == []

    asyncio.run(scenario())


def test_rpc_readiness_listener_registers_before_current_room_recheck() -> None:
    class Participant:
        def register_rpc_method(self, method, callback):
            self.callback = callback

    class RacingRoom:
        def __init__(self):
            self.remote_participants = {}
            self.listeners = []

        def on(self, event_name, callback):
            self.listeners.append(callback)
            # Simulate the join racing with listener registration, before the
            # helper checks the current room view.
            participant = SimpleNamespace(identity="person-authorized")
            self.remote_participants[participant.identity] = participant
            callback(participant)

        def off(self, event_name, callback):
            self.listeners.remove(callback)

    participant = Participant()
    room = RacingRoom()
    register_transcript_rpc(
        participant,
        history=History([]),
        authorized_identity="person-authorized",
        room=room,
        participant_wait_timeout=0.1,
    )
    response = asyncio.run(participant.callback(
        SimpleNamespace(caller_identity="person-authorized", payload="")
    ))
    assert json.loads(response) == {"messages": []}
    assert room.listeners == []


def test_rpc_readiness_timeout_is_clear_and_removes_listener() -> None:
    class Participant:
        def register_rpc_method(self, method, callback):
            self.callback = callback

    class Room:
        def __init__(self):
            self.remote_participants = {}
            self.listeners = []

        def on(self, event_name, callback):
            self.listeners.append(callback)

        def off(self, event_name, callback):
            self.listeners.remove(callback)

    participant = Participant()
    room = Room()
    history = History([])
    register_transcript_rpc(
        participant,
        history=history,
        authorized_identity="person-authorized",
        room=room,
        participant_wait_timeout=0.001,
        rpc_error_factory=lambda message: RuntimeError(message),
    )
    with pytest.raises(RuntimeError, match="not ready in the agent room; retry"):
        asyncio.run(participant.callback(SimpleNamespace(
            caller_identity="person-authorized", payload=""
        )))
    assert history.read_count == 0
    assert room.listeners == []


def test_rpc_requires_identity_and_participant_registration_api() -> None:
    with pytest.raises(ValueError):
        register_transcript_rpc(object(), history=History([]), authorized_identity="")
    with pytest.raises(TypeError):
        register_transcript_rpc(object(), history=History([]), authorized_identity="person-1")
