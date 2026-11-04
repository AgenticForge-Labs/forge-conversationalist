"""Optional development-only Hatchet mail notifications for a verified voice room.

This is a trusted server-side local demo seam, not a public subscriber API.
It never forwards mail content or Hatchet credentials to LiveKit participants,
robots, or the LLM; the only spoken string is a fixed generic question.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping
from uuid import UUID

from .presentation import PresentationEvent, PresentationRouter, publish_cue

logger = logging.getLogger(__name__)
_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")
SUMMARY = "New email received. Would you like a summary?"
SPOKEN_PROMPT = "You've got new mail. Would you like a summary?"
MAX_WIRE_BYTES = 4096
MAX_AGE = timedelta(minutes=2)
SPEAK_COOLDOWN_S = 45.0
NAMESPACE = "agenticforge-development-v1"


@dataclass(frozen=True)
class MailStreamConfig:
    business_id: str

    def __post_init__(self) -> None:
        try:
            parsed = UUID(self.business_id)
        except (ValueError, TypeError):
            raise ValueError("mail notification business ID must be a UUID") from None
        if str(parsed) != self.business_id:
            raise ValueError("mail notification business ID must be a canonical UUID")

    @property
    def topic(self) -> str:
        return f"af.{self.business_id}.company"


def load_mail_stream_config(environ: Mapping[str, str] | None = None) -> MailStreamConfig | None:
    env = os.environ if environ is None else environ
    enabled = env.get("CONVERSATIONALIST_MAIL_STREAMS", "").strip().lower()
    if enabled in {"", "0", "false", "no"}:
        return None
    if enabled not in {"1", "true", "yes"}:
        raise ValueError("CONVERSATIONALIST_MAIL_STREAMS must be a boolean")
    if env.get("APP_ENV") != "development" or not env.get("HATCHET_CLIENT_TOKEN"):
        raise ValueError("mail Streams is only supported with an isolated development Hatchet token")
    return MailStreamConfig(business_id=env.get("CONVERSATIONALIST_MAIL_STREAM_BUSINESS_ID", ""))


@dataclass(frozen=True)
class MailNotice:
    event_id: str
    observed_at: datetime


def parse_mail_notice(raw: bytes | str, *, business_id: str, now: datetime) -> MailNotice | None:
    """Reject mismatched, old or malformed notices without logging private source data."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("mail notice clock must be timezone-aware")
    if not isinstance(raw, (bytes, str)) or len(raw if isinstance(raw, bytes) else raw.encode()) > MAX_WIRE_BYTES:
        return None
    try:
        payload: Any = json.loads(raw)
    except (UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    safe_keys = {
        "schema_version", "event_id", "business_id", "source_system",
        "source_record_collection", "source_record_id", "kind", "sensitivity",
        "occurred_at", "observed_at", "summary", "correlation_id",
        "evidence_refs", "robot_id", "robot_status",
    }
    if set(payload).difference(safe_keys):
        return None
    if (
        payload.get("schema_version") != "agenticforge.event.v1"
        or payload.get("business_id") != business_id
        or payload.get("source_system") != "zoho-mail"
        or payload.get("kind") != "mail.received"
        or payload.get("source_record_collection") != "af_events"
        or payload.get("sensitivity") != "internal"
        or payload.get("summary") != SUMMARY
        or payload.get("robot_id") is not None
        or payload.get("robot_status") is not None
    ):
        return None
    event_id = payload.get("event_id")
    record_id = payload.get("source_record_id")
    if (
        not isinstance(event_id, str) or not _ID.fullmatch(event_id)
        or not isinstance(record_id, str) or not _ID.fullmatch(record_id)
        or event_id != record_id
    ):
        return None
    raw_observed = payload.get("observed_at")
    if not isinstance(raw_observed, str):
        return None
    try:
        observed = datetime.fromisoformat(raw_observed.replace("Z", "+00:00"))
    except ValueError:
        return None
    if observed.tzinfo is None or observed.utcoffset() is None:
        return None
    age = now.astimezone(UTC) - observed.astimezone(UTC)
    if age < -timedelta(seconds=15) or age > MAX_AGE:
        return None
    return MailNotice(event_id=event_id, observed_at=observed)


class AmbientMailNotifications:
    """Runs only inside one signed LiveKit room's trusted Python worker.

    The shared Hatchet token is owned by the worker, never by the browser,
    model toolset or a robot. Initial subscription skips the existing backlog.
    This is a live-only demonstration; Platform must eventually own a durable
    per-subscriber checkpoint and authorization proxy for production.
    """

    def __init__(self, config: MailStreamConfig) -> None:
        self.config = config
        self._recent = deque(maxlen=256)
        self._recent_set: set[str] = set()
        self._last_spoken = -10_000_000.0
        self._presentation = PresentationRouter()
        self._task: asyncio.Task[None] | None = None

    def start(self, session: Any, room: Any) -> None:
        if self._task is not None:
            raise RuntimeError("mail stream subscriber already started")
        self._task = asyncio.create_task(self._run(session, room), name="mail-stream-room-notifications")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self, session: Any, room: Any) -> None:
        try:
            # Optional import: ordinary voice rooms never require Hatchet.
            from hatchet_sdk import Hatchet

            hatchet = Hatchet()
            await self.consume(hatchet.streams, session, room)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The room stays operational. No implicit reconnect/rearm or
            # alternative untrusted transport is started on failure.
            logger.exception("private mail Streams notification subscriber unavailable")

    async def consume(self, streams: Any, session: Any, room: Any) -> None:
        """Subscribe to new messages only; do not speak historical mail."""
        cursor = None
        try:
            metadata = await streams.aio_topic_metadata(self.config.topic, namespace=NAMESPACE)
            cursor = getattr(metadata, "latest_cursor", None)
            if cursor is not None and not isinstance(cursor, str):
                raise ValueError("Hatchet topic metadata returned an invalid cursor")
        except Exception as exc:
            # Absence of a never-used topic is expected; other transport
            # failures must not silently start replay from the beginning.
            code = getattr(exc, "code", None)
            value = code() if callable(code) else code
            if getattr(value, "name", None) != "NOT_FOUND":
                raise
        async for record in streams.subscribe(self.config.topic, namespace=NAMESPACE, cursor=cursor):
            notice = parse_mail_notice(
                record.payload, business_id=self.config.business_id,
                now=datetime.now(UTC),
            )
            if notice is None or notice.event_id in self._recent_set:
                continue
            self._seen(notice.event_id)
            await self._notify(notice, session, room)

    def _seen(self, event_id: str) -> None:
        if len(self._recent) == self._recent.maxlen:
            self._recent_set.discard(self._recent.popleft())
        self._recent.append(event_id)
        self._recent_set.add(event_id)

    async def _notify(self, notice: MailNotice, session: Any, room: Any) -> None:
        cue = self._presentation.accept(PresentationEvent(
            event_id=notice.event_id, category="notification", value="mail", privacy="private",
        ))
        if cue is not None:
            try:
                await publish_cue(room, cue)
            except Exception:
                logger.exception("authorized mail notification UI cue failed")
        # Never interrupt a user or a prior assistant response for routine mail.
        loop = asyncio.get_running_loop()
        if loop.time() - self._last_spoken < SPEAK_COOLDOWN_S:
            return
        for _ in range(30):
            if (
                getattr(session, "agent_state", "listening") in {"idle", "listening"}
                and getattr(session, "user_state", "listening") != "speaking"
            ):
                break
            await asyncio.sleep(0.5)
        else:
            return
        # A generic opt-in spoken question, not automatic reading of mail.
        # Conversationalist's existing authorized tools answer an explicit yes.
        self._last_spoken = loop.time()
        try:
            await session.say(SPOKEN_PROMPT)
        except Exception:
            logger.exception("generic mail question TTS unavailable; no automatic retry")
