"""Offline tests check model request assembly without running audio or cloud services."""

import asyncio
import pickle
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from forge_conversationalist.agent import (
    _default_tts_sample_rate,
    _profile_for_job,
    _register_voice_rpc,
    _requested_qwen_voice,
    _requested_turn_pacing,
    _requested_vad_sensitivity,
    build_voice_session,
    entrypoint,
)
from forge_conversationalist.config import (
    OPENROUTER_DEFAULT_TTS,
    OPENROUTER_DEFAULT_VOICE,
    OPENROUTER_LEGACY_QWEN_TTS,
    CloudProfile,
    load_profile,
)
from forge_conversationalist.integrations import Integrations


def test_job_entrypoint_is_importable_and_picklable() -> None:
    assert entrypoint.__qualname__ == "entrypoint"
    assert pickle.loads(pickle.dumps(entrypoint)) is entrypoint


def test_entrypoint_uses_tts_only_greeting_without_an_llm_request() -> None:
    lifecycle: list[str] = []

    async def connect() -> None:
        lifecycle.append("connect")

    async def start(**_kwargs: object) -> None:
        lifecycle.append("start")

    session = SimpleNamespace(
        start=AsyncMock(side_effect=start), say=AsyncMock(), generate_reply=Mock(), on=Mock()
    )
    with (
        patch("forge_conversationalist.agent.load_profile", return_value=CloudProfile()),
        patch("forge_conversationalist.agent.load_integrations", return_value=Integrations()),
        patch("forge_conversationalist.agent.build_voice_session", return_value=session),
        patch("forge_conversationalist.agent.build_assistant_tools", return_value=[]),
    ):
        context = SimpleNamespace(room="room", job=SimpleNamespace(metadata=""), connect=AsyncMock(side_effect=connect))
        asyncio.run(entrypoint(context))

    context.connect.assert_awaited_once()
    session.start.assert_awaited_once()
    assert lifecycle == ["connect", "start"]
    assert session.start.await_args.kwargs["room_options"].close_on_disconnect is False
    assert {call.args[0] for call in session.on.call_args_list} == {
        "conversation_item_added", "agent_state_changed", "user_state_changed"
    }
    session.say.assert_awaited_once_with("Hello! I'm here to help.")
    session.generate_reply.assert_not_called()



def test_playback_presence_opt_in_attaches_to_livekit_output_after_start() -> None:
    listeners = {}

    class Output:
        def on(self, name, callback):
            listeners.setdefault(name, []).append(callback)

        def off(self, name, callback):
            listeners[name].remove(callback)

    output = Output()
    order = []

    async def say(*_args, **_kwargs):
        order.append("greeting")

    async def wait_for_startup(*_args, **_kwargs):
        order.append("arm_ready")
        return True

    session = SimpleNamespace(
        start=AsyncMock(), say=say, on=Mock(), history=[],
        output=SimpleNamespace(audio=output),
    )
    robot = SimpleNamespace(
        start=AsyncMock(return_value={"state": "disconnected"}), close=AsyncMock(),
        observe=Mock(), wait_for_startup=wait_for_startup,
        status=AsyncMock(return_value={"performers": {}}),
    )
    room = SimpleNamespace(name="room", local_participant=Mock())
    ctx = SimpleNamespace(
        room=room, job=SimpleNamespace(metadata='{"authorized_identity":"person"}'),
        connect=AsyncMock(), add_shutdown_callback=Mock(),
    )
    with (
        patch("forge_conversationalist.agent.load_profile", return_value=CloudProfile()),
        patch("forge_conversationalist.agent.load_integrations",
              return_value=Integrations(ensemble=object())),
        patch("forge_conversationalist.agent.build_voice_session", return_value=session),
        patch("forge_conversationalist.agent.EnsembleSession", return_value=robot),
        patch("forge_conversationalist.agent.build_assistant_tools", return_value=[]),
        patch("forge_conversationalist.agent.register_transcript_rpc"),
        patch("forge_conversationalist.agent._register_voice_rpc"),
        patch("forge_conversationalist.agent.register_robot_rpc"),
        patch.dict("os.environ", {"CONVERSATIONALIST_PLAYBACK_PRESENCE": "1"}),
    ):
        asyncio.run(entrypoint(ctx))
    session.start.assert_awaited_once()
    # No vocal greeting until the arm has finished waking up.
    assert order == ["arm_ready", "greeting"]
    # Attentive (a short hold) while it wakes, rather than starting a long breath.
    assert robot.observe.call_args_list[0].args == ("listening",)
    assert set(listeners) == {
        "playback_started", "playback_finished", "playback_progressed",
    }
    listeners["playback_started"][0](SimpleNamespace(created_at=123.0))
    robot.observe.assert_called_with("speaking", source="playback", interrupted=False)
    listeners["playback_finished"][0](
        SimpleNamespace(playback_position=0.6, interrupted=False)
    )
    robot.observe.assert_called_with("idle", source="playback", interrupted=False)


def test_ensemble_startup_failure_keeps_voice_and_removes_robot_tools() -> None:
    session = SimpleNamespace(start=AsyncMock(), say=AsyncMock(), on=Mock(), history=[])
    robot = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("unavailable")),
                            close=AsyncMock())
    tool_builder = Mock(return_value=[])
    room = SimpleNamespace(name="room", local_participant=Mock())
    ctx = SimpleNamespace(
        room=room, job=SimpleNamespace(metadata='{"authorized_identity":"person"}'),
        connect=AsyncMock(), add_shutdown_callback=Mock(),
    )
    with (
        patch("forge_conversationalist.agent.load_profile", return_value=CloudProfile()),
        patch("forge_conversationalist.agent.load_integrations", return_value=Integrations(ensemble=object())),
        patch("forge_conversationalist.agent.build_voice_session", return_value=session),
        patch("forge_conversationalist.agent.EnsembleSession", return_value=robot),
        patch("forge_conversationalist.agent.build_assistant_tools", tool_builder),
        patch("forge_conversationalist.agent.register_transcript_rpc"),
        patch("forge_conversationalist.agent._register_voice_rpc"),
    ):
        asyncio.run(entrypoint(ctx))
    robot.start.assert_awaited_once()
    session.start.assert_awaited_once()
    assert tool_builder.call_args.args[0].ensemble is None
    assert tool_builder.call_args.kwargs["ensemble_session"] is None
    session.say.assert_awaited_once()


def test_unverified_original_speech_association_keeps_voice_without_robot_startup() -> None:
    session = SimpleNamespace(start=AsyncMock(), say=AsyncMock(), on=Mock(), history=[])
    tool_builder = Mock(return_value=[])
    room = SimpleNamespace(name="room", local_participant=Mock())
    ctx = SimpleNamespace(
        room=room, job=SimpleNamespace(metadata='{"authorized_identity":"person"}'),
        connect=AsyncMock(), add_shutdown_callback=Mock(),
    )
    original = SimpleNamespace(motion_profiles={"reachy": {"speaking": "original_v1"}})
    with (
        patch("forge_conversationalist.agent.load_profile", return_value=CloudProfile()),
        patch("forge_conversationalist.agent.load_integrations", return_value=Integrations(ensemble=original)),
        patch("forge_conversationalist.agent._identified_output_sdk_available", return_value=False),
        patch("forge_conversationalist.agent.build_voice_session", return_value=session),
        patch("forge_conversationalist.agent.EnsembleSession") as robot_type,
        patch("forge_conversationalist.agent.build_assistant_tools", tool_builder),
        patch("forge_conversationalist.agent.register_transcript_rpc"),
        patch("forge_conversationalist.agent._register_voice_rpc"),
    ):
        asyncio.run(entrypoint(ctx))
    robot_type.assert_not_called()
    session.start.assert_awaited_once()
    session.say.assert_awaited_once()
    assert tool_builder.call_args.kwargs["ensemble_session"] is None


@pytest.mark.parametrize(("output_supported", "preinterrupt"), [(True, False), (True, True), (False, False)])
def test_original_speech_registers_robot_tools_only_after_output_verification(output_supported, preinterrupt) -> None:
    listeners = {}
    session_listeners = {}

    class Output:
        identified_playback_supported = output_supported

        def on(self, name, callback):
            listeners.setdefault(name, []).append(callback)

        def off(self, name, callback):
            listeners[name].remove(callback)

    lifecycle = []
    agent = SimpleNamespace(update_tools=AsyncMock(), update_instructions=AsyncMock())

    async def start_session(**_kwargs):
        lifecycle.append("session_started")
        if preinterrupt:
            session_listeners["agent_state_changed"](SimpleNamespace(new_state="speaking"))
            session_listeners["user_state_changed"](SimpleNamespace(new_state="speaking"))

    async def update_tools(_tools):
        lifecycle.append("robot_tools_enabled")

    agent.update_tools.side_effect = update_tools
    session = SimpleNamespace(
        start=AsyncMock(side_effect=start_session), say=AsyncMock(),
        on=lambda name, callback: session_listeners.__setitem__(name, callback), history=[],
        output=SimpleNamespace(audio=Output()), agent_state="idle",
    )
    robot = SimpleNamespace(
        start=AsyncMock(return_value={"state": "disconnected"}), close=AsyncMock(),
        observe=Mock(), wait_for_startup=AsyncMock(return_value=True),
        status=AsyncMock(return_value={"performers": {}}),
        associate_native_playback=Mock(), begin_native_gain=Mock(),
        offer_native_gain=Mock(), cancel_native_gain=Mock(),
    )
    room = SimpleNamespace(name="room", local_participant=Mock())
    ctx = SimpleNamespace(
        room=room, job=SimpleNamespace(metadata='{"authorized_identity":"person"}'),
        connect=AsyncMock(), add_shutdown_callback=Mock(),
    )
    original = SimpleNamespace(motion_profiles={"reachy": {"speaking": "original_v1"}})
    with (
        patch("forge_conversationalist.agent.load_profile", return_value=CloudProfile()),
        patch("forge_conversationalist.agent.load_integrations",
              return_value=Integrations(ensemble=original)),
        patch("forge_conversationalist.agent._identified_output_sdk_available", return_value=True),
        patch("forge_conversationalist.agent.build_voice_session", return_value=session),
        patch("forge_conversationalist.agent.EnsembleSession", return_value=robot),
        patch("forge_conversationalist.agent.agent_class_with_speech_envelope",
              return_value=lambda **_kwargs: agent),
        patch("forge_conversationalist.agent.build_assistant_tools", return_value=[]),
        patch("forge_conversationalist.agent.register_transcript_rpc"),
        patch("forge_conversationalist.agent._register_voice_rpc"),
        patch("forge_conversationalist.agent.register_robot_rpc") as register_robot,
    ):
        asyncio.run(entrypoint(ctx))
    assert lifecycle[0] == "session_started"
    session.say.assert_awaited_once()
    if output_supported:
        assert lifecycle == ["session_started", "robot_tools_enabled"]
        agent.update_instructions.assert_awaited_once()
        register_robot.assert_called_once()
        if preinterrupt:
            robot.observe.assert_not_called()
        else:
            robot.observe.assert_called_once_with("idle")
        assert "identified_playback_started" in listeners
    else:
        agent.update_tools.assert_not_awaited()
        register_robot.assert_not_called()
        robot.close.assert_awaited_once()
        robot.observe.assert_not_called()


def test_legacy_qwen_voice_metadata_stays_pinned_to_its_model() -> None:
    assert _requested_qwen_voice('{"tts_voice":"longanhuan_v3.6"}') == "longanhuan_v3.6"
    assert _requested_qwen_voice('{"tts_voice":"untrusted-value"}') is None
    assert _requested_qwen_voice('{"tts_voice":[]}') is None
    assert _requested_qwen_voice("not json") is None

    openrouter = load_profile({
        "CONVERSATIONALIST_PRESET": "openrouter",
        "CONVERSATIONALIST_TTS_MODEL": OPENROUTER_LEGACY_QWEN_TTS,
    })
    chosen = _profile_for_job(openrouter, '{"tts_voice":"longanhuan_v3.6"}')
    assert chosen.tts_voice == "longanhuan_v3.6"
    default_profile = load_profile({"CONVERSATIONALIST_PRESET": "openrouter"})
    assert _profile_for_job(default_profile, '{"tts_voice":"longanhuan_v3.6"}') is default_profile
    assert _requested_qwen_voice('{"tts_voice":"lucian"}') is None

    other_model = CloudProfile(
        tts_provider="openrouter",
        tts_model="other/provider-model",
        tts_voice=OPENROUTER_DEFAULT_VOICE,
    )
    assert _profile_for_job(other_model, '{"tts_voice":"longanhuan_v3.6"}') is other_model
    livekit_tts = CloudProfile(tts_voice="provider/default")
    assert _profile_for_job(livekit_tts, '{"tts_voice":"longanhuan_v3.6"}') is livekit_tts
    assert openrouter.tts_model == OPENROUTER_LEGACY_QWEN_TTS


def test_known_default_tts_rate_does_not_require_catalog_access() -> None:
    profile = load_profile({"CONVERSATIONALIST_PRESET": "openrouter"})
    assert profile.tts_model == OPENROUTER_DEFAULT_TTS
    assert _default_tts_sample_rate(profile) == 44100
    assert _default_tts_sample_rate(CloudProfile(tts_provider="openrouter", tts_model="other/provider-model")) == 24000


def test_job_vad_sensitivity_accepts_only_named_presets() -> None:
    assert _requested_vad_sensitivity('{"vad_sensitivity":"less"}') == "less"
    assert _requested_vad_sensitivity('{"vad_sensitivity":"more"}') == "more"
    assert _requested_vad_sensitivity('{"vad_sensitivity":[]}') == "standard"
    assert _requested_vad_sensitivity('{"vad_sensitivity":"unknown"}') == "standard"
    assert _requested_vad_sensitivity("not json") == "standard"


@pytest.mark.parametrize(("value", "expected"), [
    ('{"turn_pacing":"quick"}', "quick"),
    ('{"turn_pacing":"patient"}', "patient"),
    ('{"turn_pacing":[]}', "standard"),
    ('{"turn_pacing":"invalid"}', "standard"),
])
def test_turn_pacing_restricts_worker_metadata(value, expected):
    assert _requested_turn_pacing(value) == expected


@pytest.mark.parametrize(("pacing", "delay"), [("quick", 0.25), ("patient", 1.2)])
def test_turn_pacing_configures_livekit_endpointing(pacing, delay):
    constructors = SimpleNamespace(
        STT=MagicMock(), LLM=MagicMock(), TTS=MagicMock(), TurnDetector=MagicMock(),
    )
    sdk = SimpleNamespace(inference=constructors, AgentSession=MagicMock(side_effect=lambda **x: x),
                          TurnHandlingOptions=MagicMock(side_effect=lambda **x: x))
    fake_silero = SimpleNamespace(VAD=SimpleNamespace(load=MagicMock()))
    with patch.dict("sys.modules", {"livekit.plugins.silero": fake_silero}):
        session = build_voice_session(CloudProfile(), sdk=sdk, turn_pacing=pacing)
    assert session["turn_handling"]["endpointing"] == {
        "mode": "fixed", "min_delay": delay, "max_delay": delay,
    }


def test_live_voice_rpc_updates_tts_for_only_authorized_participant() -> None:
    handlers: dict[str, object] = {}
    local_participant = SimpleNamespace(
        register_rpc_method=lambda name, handler: handlers.__setitem__(name, handler)
    )
    agent = SimpleNamespace(update_options=Mock())
    model = {
        "id": "hexgrad/kokoro-82m",
        "responseFormat": "mp3",
        "sampleRate": 24000,
    }
    base_profile = load_profile({
        "CONVERSATIONALIST_PRESET": "openrouter",
        "CONVERSATIONALIST_TTS_MODEL": "qwen/qwen-audio-3.0-tts-flash",
    })
    initial_profile = load_profile({"CONVERSATIONALIST_PRESET": "openrouter"})
    with (
        patch("forge_conversationalist.agent.selected_speech_model", return_value=model),
        patch("forge_conversationalist.agent._build_tts", return_value="new-tts") as build_tts,
    ):
        _register_voice_rpc(
            local_participant,
            caller_identity="person-authorized",
            agent=agent,
            base_profile=base_profile,
            current_profile=initial_profile,
        )
        get_handler = handlers["forge.get_speech"]
        initial = asyncio.run(get_handler(SimpleNamespace(caller_identity="person-authorized", payload="{}")))
        handler = handlers["forge.change_speech"]
        response = asyncio.run(handler(SimpleNamespace(
            caller_identity="person-authorized",
            payload='{"tts_model":"hexgrad/kokoro-82m","tts_voice":"af_heart"}',
        )))
        updated = asyncio.run(get_handler(SimpleNamespace(caller_identity="person-authorized", payload="{}")))
        reset = asyncio.run(handler(SimpleNamespace(
            caller_identity="person-authorized",
            payload='{"tts_model":null,"tts_voice":null}',
        )))
        after_reset = asyncio.run(get_handler(SimpleNamespace(caller_identity="person-authorized", payload="{}")))

    assert initial == '{"model": "heygen/heygen-voice-1-turbo", "voice": "lucian"}'
    assert response == '{"model": "hexgrad/kokoro-82m", "voice": "af_heart"}'
    assert updated == response
    assert reset == '{"model": "qwen/qwen-audio-3.0-tts-flash", "voice": "loongjohn"}'
    assert after_reset == reset
    assert build_tts.call_count == 2
    assert agent.update_options.call_count == 2


def test_get_speech_rpc_rejects_unauthorized_room_participants() -> None:
    handlers: dict[str, object] = {}
    local_participant = SimpleNamespace(
        register_rpc_method=lambda name, handler: handlers.__setitem__(name, handler)
    )
    _register_voice_rpc(
        local_participant,
        caller_identity="person-authorized",
        agent=SimpleNamespace(update_options=Mock()),
        base_profile=load_profile({"CONVERSATIONALIST_PRESET": "openrouter"}),
        current_profile=CloudProfile(
            tts_provider="openrouter",
            tts_model="hexgrad/kokoro-82m",
            tts_voice="af_heart",
        ),
    )
    with pytest.raises(Exception, match="Only the authorized"):
        asyncio.run(handlers["forge.get_speech"](SimpleNamespace(
            caller_identity="person-other",
            payload="{}",
        )))


def test_live_voice_rpc_rejects_other_room_participants() -> None:
    handlers: dict[str, object] = {}
    local_participant = SimpleNamespace(
        register_rpc_method=lambda name, handler: handlers.__setitem__(name, handler)
    )
    with patch("forge_conversationalist.agent._build_tts") as build_tts:
        _register_voice_rpc(
            local_participant,
            caller_identity="person-authorized",
            agent=SimpleNamespace(update_options=Mock()),
            base_profile=CloudProfile(),
        )
        handler = handlers["forge.change_speech"]
        with pytest.raises(Exception, match="Only the authorized"):
            asyncio.run(handler(SimpleNamespace(
                caller_identity="person-other",
                payload='{"tts_model":"hexgrad/kokoro-82m","tts_voice":"af_heart"}',
            )))
    build_tts.assert_not_called()


def test_session_uses_profile_and_livekit_owned_vad_turns() -> None:
    constructors = SimpleNamespace(
        STT=MagicMock(side_effect=lambda **x: ("stt", x)),
        LLM=MagicMock(side_effect=lambda **x: ("llm", x)),
        TTS=MagicMock(side_effect=lambda **x: ("tts", x)),
        TurnDetector=MagicMock(return_value="detector"),
    )
    sdk = SimpleNamespace(
        inference=constructors,
        AgentSession=MagicMock(side_effect=lambda **x: x),
        TurnHandlingOptions=MagicMock(side_effect=lambda **x: x),
    )
    fake_silero = SimpleNamespace(VAD=SimpleNamespace(load=MagicMock(return_value="silero")))

    with patch.dict("sys.modules", {"livekit.plugins.silero": fake_silero}):
        session = build_voice_session(
            CloudProfile(stt_model="provider/stt", llm_model="provider/llm",
                         tts_model="provider/tts", tts_voice="voice"),
            sdk=sdk,
        )

    assert session["stt"] == ("stt", {"model": "provider/stt", "language": "en"})
    assert session["llm"] == ("llm", {"model": "provider/llm"})
    assert session["tts"] == ("tts", {"model": "provider/tts", "voice": "voice"})
    assert session["vad"] == "silero"
    assert session["turn_handling"] == {"turn_detection": "detector", "preemptive_generation": {"enabled": True, "preemptive_tts": False}}


@pytest.mark.parametrize(("sensitivity", "expected_kwargs"), [
    ("standard", {}),
    ("less", {"activation_threshold": 0.65}),
    ("more", {"activation_threshold": 0.35}),
])
def test_vad_sensitivity_maps_to_silero_activation_threshold(
    sensitivity: str, expected_kwargs: dict[str, float]
) -> None:
    vad_load = MagicMock(return_value="silero")
    sdk = SimpleNamespace(
        inference=SimpleNamespace(
            STT=MagicMock(return_value="stt"),
            LLM=MagicMock(return_value="llm"),
            TTS=MagicMock(return_value="tts"),
            TurnDetector=MagicMock(return_value="detector"),
        ),
        AgentSession=MagicMock(side_effect=lambda **kwargs: kwargs),
        TurnHandlingOptions=MagicMock(side_effect=lambda **kwargs: kwargs),
    )
    silero = SimpleNamespace(VAD=SimpleNamespace(load=vad_load))
    profile = load_profile({})
    with patch.dict("sys.modules", {"livekit.plugins.silero": silero}):
        build_voice_session(profile, vad_sensitivity=sensitivity, sdk=sdk)
    vad_load.assert_called_once_with(**expected_kwargs)


def test_vad_sensitivity_rejects_unknown_preset() -> None:
    sdk = SimpleNamespace()
    with pytest.raises(ValueError, match="vad_sensitivity"):
        build_voice_session(load_profile({}), vad_sensitivity="noise-canceling", sdk=sdk)
