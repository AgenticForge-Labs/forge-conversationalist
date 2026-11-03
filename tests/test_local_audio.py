"""Offline checks for the browserless LiveKit audio participant."""

import asyncio

import jwt
import pytest

from forge_conversationalist.local_audio import (
    build_local_room,
    is_agent_audio,
    resolve_device,
    wait_for_agent,
)


def test_local_room_token_dispatches_same_authorized_audio_participant():
    room = build_local_room(
        api_key="local-test-key",
        api_secret="local-test-secret-that-is-long-enough-for-hmac",
        agent_name="forge-conversationalist",
    )

    claims = jwt.decode(room.token, options={"verify_signature": False})
    assert claims["sub"] == room.identity
    assert claims["video"]["room"] == room.name
    assert claims["video"]["roomJoin"] is True
    assert claims["video"]["canPublish"] is True
    assert claims["video"]["canSubscribe"] is True
    dispatch = claims["roomConfig"]["agents"][0]
    assert dispatch["agentName"] == "forge-conversationalist"
    assert dispatch["metadata"] == '{"authorized_identity":"' + room.identity + '"}'
    assert room.name.startswith("conversationalist-local-")


def test_device_selection_uses_system_defaults_when_unset():
    assert resolve_device([], None, kind="output") is None


def test_device_selection_accepts_index_or_unique_name():
    devices = [
        {"index": 2, "name": "System Speakers"},
        {"index": 7, "name": "alsa_output.usb-Pollen_Robotics_Reachy_Mini_Audio"},
    ]
    assert resolve_device(devices, "7", kind="output") == 7
    assert resolve_device(devices, "reachy mini", kind="output") == 7


def test_device_selection_rejects_missing_or_ambiguous_override():
    devices = [
        {"index": 1, "name": "USB Speaker Left"},
        {"index": 2, "name": "USB Speaker Right"},
    ]
    with pytest.raises(ValueError, match="not available"):
        resolve_device(devices, "9", kind="output")
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_device(devices, "USB Speaker", kind="output")


def test_speaker_routes_agent_audio_only():
    class Kinds:
        class TrackKind:
            KIND_AUDIO = 1

        class ParticipantKind:
            PARTICIPANT_KIND_AGENT = 4

    assert is_agent_audio(1, 4, rtc=Kinds)
    assert not is_agent_audio(1, 0, rtc=Kinds)
    assert not is_agent_audio(2, 4, rtc=Kinds)


def test_wait_for_agent_completes_when_agent_joins():
    async def run():
        ready = asyncio.Event()
        stopping = asyncio.Event()
        ready.set()
        await wait_for_agent(ready, stopping, timeout=0.01)

    asyncio.run(run())

def test_wait_for_agent_aborts_if_room_disconnects_first():
    async def run():
        ready = asyncio.Event()
        stopping = asyncio.Event()
        stopping.set()
        with pytest.raises(ConnectionError, match="disconnected"):
            await wait_for_agent(ready, stopping, timeout=0.01)

    asyncio.run(run())


def test_wait_for_agent_times_out_before_opening_microphone():
    async def run():
        with pytest.raises(TimeoutError, match="did not join"):
            await wait_for_agent(asyncio.Event(), asyncio.Event(), timeout=0.001)

    asyncio.run(run())
