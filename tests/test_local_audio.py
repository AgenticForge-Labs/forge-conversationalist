"""Offline checks for the browserless LiveKit audio participant."""

import asyncio

import jwt
import pytest

from forge_conversationalist.local_audio import (
    _open_duplex_audio,
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


def test_duplex_audio_opens_input_before_output_for_echo_cancellation():
    async def run():
        calls = []

        class InputCapture:
            async def aclose(self):
                calls.append("input_closed")

        class OutputPlayer:
            async def start(self):
                calls.append("output_started")

        class Media:
            def open_input(self, **kwargs):
                calls.append(("input", kwargs))
                return InputCapture()

            def open_output(self, **kwargs):
                calls.append(("output", kwargs))
                return OutputPlayer()

        capture, player = await _open_duplex_audio(
            Media(), input_device=6, output_device=None,
        )
        assert isinstance(capture, InputCapture)
        assert isinstance(player, OutputPlayer)
        assert calls[0] == ("input", {
            "input_device": 6,
            "enable_aec": True,
            "noise_suppression": True,
            "high_pass_filter": True,
            "auto_gain_control": True,
        })
        assert calls[1] == ("output", {"output_device": None})
        assert calls[2] == "output_started"

    asyncio.run(run())


def test_duplex_audio_closes_input_if_output_setup_fails():
    async def run():
        calls = []

        class InputCapture:
            async def aclose(self):
                calls.append("input_closed")

        class Media:
            def open_input(self, **_kwargs):
                return InputCapture()

            def open_output(self, **_kwargs):
                raise RuntimeError("speaker unavailable")

        with pytest.raises(RuntimeError, match="speaker unavailable"):
            await _open_duplex_audio(Media(), input_device=6, output_device=None)
        assert calls == ["input_closed"]

    asyncio.run(run())


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
