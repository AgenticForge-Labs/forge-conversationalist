"""Offline checks for authorized ambient mail notification presentation."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from forge_conversationalist.mail_stream_notifications import (
    SUMMARY,
    AmbientMailNotifications,
    MailStreamConfig,
    load_mail_stream_config,
    parse_mail_notice,
)


BUSINESS = "12345678-1234-4234-9234-123456789012"


def wire(*, event_id="evt-42", **extra):
    event = {
        "schema_version": "agenticforge.event.v1",
        "event_id": event_id,
        "business_id": BUSINESS,
        "source_system": "zoho-mail",
        "source_record_collection": "af_events",
        "source_record_id": event_id,
        "kind": "mail.received",
        "sensitivity": "internal",
        "occurred_at": datetime.now(UTC).isoformat(),
        "observed_at": datetime.now(UTC).isoformat(),
        "summary": SUMMARY,
        "correlation_id": None,
        "evidence_refs": [],
        "robot_id": None,
        "robot_status": None,
    }
    event.update(extra)
    return json.dumps(event)


def test_explicit_dev_token_and_company_scope_required():
    assert load_mail_stream_config({}) is None
    assert load_mail_stream_config({"CONVERSATIONALIST_MAIL_STREAMS": "false"}) is None
    enabled = {
        "CONVERSATIONALIST_MAIL_STREAMS": "1",
        "APP_ENV": "development",
        "HATCHET_CLIENT_TOKEN": "synthetic-private-token",
        "CONVERSATIONALIST_MAIL_STREAM_BUSINESS_ID": BUSINESS,
    }
    assert load_mail_stream_config(enabled).topic == f"af.{BUSINESS}.company"
    for change in (
        {"APP_ENV": "production"},
        {"HATCHET_CLIENT_TOKEN": ""},
        {"CONVERSATIONALIST_MAIL_STREAM_BUSINESS_ID": "different-company"},
    ):
        with pytest.raises(ValueError):
            load_mail_stream_config({**enabled, **change})


def test_parser_is_tenant_scoped_and_rejects_private_mail_data():
    assert parse_mail_notice(wire(), business_id=BUSINESS, now=datetime.now(UTC)) is not None
    for change in (
        {"business_id": "other-company"},
        {"source_system": "forged-client"},
        {"kind": "robot.status"},
        {"sensitivity": "public"},
        {"sender": "secret@example.com"},
        {"subject": "Confidential matter"},
        {"summary": "Confidential email subject"},
        {"robot_id": "reachy-mini"},
        {"event_id": "bad id"},
        {"source_record_id": "other"},
        {"schema_version": "unsupported"},
    ):
        assert parse_mail_notice(wire(**change), business_id=BUSINESS, now=datetime.now(UTC)) is None
    assert parse_mail_notice(wire() + "x" * 10000, business_id=BUSINESS, now=datetime.now(UTC)) is None


def test_parser_rejects_stale_or_future_events_and_naive_clock():
    for moment in (datetime.now(UTC) - timedelta(minutes=10), datetime.now(UTC) + timedelta(minutes=3)):
        assert parse_mail_notice(wire(observed_at=moment.isoformat()), business_id=BUSINESS, now=datetime.now(UTC)) is None
    with pytest.raises(ValueError):
        parse_mail_notice(wire(), business_id=BUSINESS, now=datetime.now().replace(tzinfo=None))


class FakeStreams:
    def __init__(self, raws):
        self.raws = raws
        self.calls = []

    async def aio_topic_metadata(self, topic, *, namespace):
        self.calls.append(("metadata", topic, namespace))
        return SimpleNamespace(latest_cursor="existing-latest")

    async def subscribe(self, topic, *, namespace, cursor):
        self.calls.append(("subscribe", topic, namespace, cursor))
        for text in self.raws:
            yield SimpleNamespace(payload=text, cursor="new-event")


@pytest.mark.asyncio
async def test_room_emits_only_generic_mail_visual_and_spoken_prompt():
    client = AmbientMailNotifications(MailStreamConfig(BUSINESS))
    streams = FakeStreams([wire(), wire(), wire(event_id="evt-43")])
    participant = SimpleNamespace(publish_data=AsyncMock())
    room = SimpleNamespace(local_participant=participant)
    session = SimpleNamespace(say=AsyncMock(), agent_state="listening", user_state="listening")
    await client.consume(streams, session, room)
    assert streams.calls[1][-1] == "existing-latest"
    assert participant.publish_data.await_count == 2
    visual_payload = json.loads(participant.publish_data.await_args_list[0].args[0])
    assert visual_payload["schema"] == "forge.presentation.v1"
    assert visual_payload["value"] == "mail"
    assert visual_payload["privacy"] == "private"
    assert visual_payload["auto_speak"] is False
    session.say.assert_awaited_once_with("You've got new mail. Would you like a summary?")


@pytest.mark.asyncio
async def test_busy_room_does_not_interrupt_or_read_mail():
    client = AmbientMailNotifications(MailStreamConfig(BUSINESS))
    streams = FakeStreams([wire()])
    participant = SimpleNamespace(publish_data=AsyncMock())
    room = SimpleNamespace(local_participant=participant)
    session = SimpleNamespace(say=AsyncMock(), agent_state="speaking", user_state="speaking")
    # The UI notification may be published, but speech waits while busy.
    task = asyncio.create_task(client.consume(streams, session, room))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    session.say.assert_not_called()
