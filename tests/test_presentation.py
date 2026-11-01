"""Pure semantic state and notification behavior, without devices/providers."""

import asyncio
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from forge_conversationalist.presentation import (
    SCHEMA,
    TOPIC,
    PresentationEvent,
    PresentationRouter,
    plan_cue,
    publish_cue,
)


@pytest.mark.parametrize("state", ["idle", "listening", "speaking"])
def test_conversation_state_does_not_trigger_audio_or_movement(state: str) -> None:
    cue = plan_cue(PresentationEvent(f"state-{state}", "conversation", state))
    assert cue.value == state
    assert cue.sound == "none"
    assert cue.emotion is None
    assert not cue.auto_speak


@pytest.mark.parametrize(
    "emotion",
    ["neutral", "happy", "curious", "confused", "concerned", "excited"],
)
def test_emotions_are_semantic_not_trajectories(emotion: str) -> None:
    cue = plan_cue(PresentationEvent(f"emotion-{emotion}", "emotion", emotion))
    assert cue.emotion == emotion
    assert cue.sound == "none"
    assert "motion" not in json.loads(cue.as_json())


@pytest.mark.parametrize("kind,sound", [
    ("mail", "soft_ding"), ("order", "double_ding"), ("failure", "warning"),
])
def test_notifications_have_generic_labels_without_private_details(kind: str, sound: str) -> None:
    cue = plan_cue(PresentationEvent(f"note-{kind}", "notification", kind, "private"))
    assert cue.sound == sound
    assert cue.visual_label
    assert not cue.auto_speak
    assert cue.privacy == "private"
    assert cue.category == "notification"
    assert cue.schema == SCHEMA


@pytest.mark.parametrize("bad", [
    ("", "notification", "mail"),
    ("bad id", "notification", "mail"),
    ("x", "notification", "payment_received"),
    ("x", "conversation", "thinking_loudly"),
    ("x", "emotion", "anger"),
    ("x", "robot", "dance"),
])
def test_invalid_events_fail_closed(bad: tuple[str, str, str]) -> None:
    with pytest.raises(ValueError):
        PresentationEvent(*bad)


def test_router_dedupes_and_bounds_cache() -> None:
    router = PresentationRouter(max_seen=2)
    one = PresentationEvent("id1", "notification", "mail")
    two = PresentationEvent("id2", "notification", "order")
    three = PresentationEvent("id3", "notification", "failure")
    assert router.accept(one) is not None
    assert router.accept(one) is None
    assert router.accept(two) is not None
    assert router.accept(three) is not None
    assert router.accept(one) is not None  # Ephemeral bounded cache, not durable replay.


def test_router_does_not_accept_invalid_capacity() -> None:
    with pytest.raises(ValueError):
        PresentationRouter(0)


def test_wire_format_is_bounded_and_immutable() -> None:
    event = PresentationEvent("evt:1", "notification", "failure")
    cue = plan_cue(event)
    packet = json.loads(cue.as_json())
    assert packet == {
        "schema": SCHEMA, "event_id": "evt:1", "category": "notification",
        "value": "failure", "visual_label": "Something needs attention",
        "sound": "warning", "emotion": None, "privacy": "public",
        "auto_speak": False,
    }
    with pytest.raises(FrozenInstanceError):
        cue.sound = "none"


def test_livekit_publish_is_explicit_not_automatic() -> None:
    publish_data = AsyncMock()
    room = SimpleNamespace(local_participant=SimpleNamespace(publish_data=publish_data))
    cue = plan_cue(PresentationEvent("evt-99", "notification", "order"))
    asyncio.run(publish_cue(room, cue))
    publish_data.assert_awaited_once_with(cue.as_json(), reliable=True, topic=TOPIC)
