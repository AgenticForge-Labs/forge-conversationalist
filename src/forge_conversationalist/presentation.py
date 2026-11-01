"""Hardware-independent presentation signals.

This module does not fetch mail/orders, subscribe to Platform, play sounds,
invoke speech synthesis, or send motor commands. It translates an already
authorized semantic event into bounded cues for optional renderers.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import asdict, dataclass
from typing import Literal

TOPIC = "forge.presentation.v1"
SCHEMA = "forge.presentation.v1"

Category = Literal["conversation", "emotion", "notification"]
Privacy = Literal["public", "private"]
ConversationState = Literal["idle", "listening", "speaking"]
Emotion = Literal["neutral", "happy", "curious", "confused", "concerned", "excited"]
Notification = Literal["mail", "order", "failure"]
Sound = Literal["none", "soft_ding", "double_ding", "warning"]

_ALLOWED: dict[str, frozenset[str]] = {
    "conversation": frozenset({"idle", "listening", "speaking"}),
    "emotion": frozenset({"neutral", "happy", "curious", "confused", "concerned", "excited"}),
    "notification": frozenset({"mail", "order", "failure"}),
}
_LABELS = {
    "idle": "Idle",
    "listening": "Listening",
    "speaking": "Speaking",
    "neutral": "Neutral",
    "happy": "Happy",
    "curious": "Curious",
    "confused": "Confused",
    "concerned": "Concerned",
    "excited": "Excited",
    "mail": "New mail",
    "order": "New order",
    "failure": "Something needs attention",
}
_SOUND: dict[str, Sound] = {
    "mail": "soft_ding",
    "order": "double_ding",
    "failure": "warning",
}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class PresentationEvent:
    """A typed event from an authorized producer; never claim it is a Platform fact."""

    event_id: str
    category: Category
    value: str
    privacy: Privacy = "public"

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.event_id):
            raise ValueError("event_id must be a stable, bounded opaque identifier")
        if self.category not in _ALLOWED or self.value not in _ALLOWED[self.category]:
            raise ValueError("unsupported presentation event category or value")
        if self.privacy not in {"public", "private"}:
            raise ValueError("privacy must be public or private")


@dataclass(frozen=True)
class PresentationCue:
    """Wire format; downstream renderers may ignore unsupported capabilities."""

    schema: str
    event_id: str
    category: Category
    value: str
    visual_label: str
    sound: Sound
    emotion: str | None
    privacy: Privacy
    auto_speak: bool = False

    def as_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=True)


def plan_cue(event: PresentationEvent) -> PresentationCue:
    """Deterministic mapping, no external inference or side effects."""
    return PresentationCue(
        schema=SCHEMA,
        event_id=event.event_id,
        category=event.category,
        value=event.value,
        visual_label=_LABELS[event.value],
        sound=_SOUND.get(event.value, "none") if event.category == "notification" else "none",
        emotion=event.value if event.category == "emotion" else None,
        privacy=event.privacy,
        auto_speak=False,  # Never read private mail/order contents without permission.
    )


class PresentationRouter:
    """Bounded duplicate suppression for local ephemeral delivery.

    This is NOT a durable Platform event ledger or an acknowledged queue.
    Platform retains source, replay and delivery authority in future integrations.
    """

    def __init__(self, max_seen: int = 256) -> None:
        if not 1 <= max_seen <= 4096:
            raise ValueError("max_seen must be between 1 and 4096")
        self._seen: set[str] = set()
        self._order: deque[str] = deque()
        self._limit = max_seen

    def accept(self, event: PresentationEvent) -> PresentationCue | None:
        if event.event_id in self._seen:
            return None
        self._order.append(event.event_id)
        self._seen.add(event.event_id)
        while len(self._order) > self._limit:
            self._seen.remove(self._order.popleft())
        return plan_cue(event)


async def publish_cue(room: object, cue: PresentationCue) -> None:
    """Publish to LiveKit participants when an authorized upstream caller exists.

    Not called automatically by the current agent; no Platform subscription
    exists. A client must trust the verified sender, not mere topic membership.
    """
    await room.local_participant.publish_data(
        cue.as_json(), reliable=True, topic=TOPIC
    )
