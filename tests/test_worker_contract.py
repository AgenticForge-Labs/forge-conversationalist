"""Offline tests check model request assembly without running audio or cloud services."""

import asyncio
import pickle
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from forge_conversationalist.agent import (
    _profile_for_job,
    _requested_qwen_voice,
    _requested_vad_sensitivity,
    build_voice_session,
    entrypoint,
)
from forge_conversationalist.config import (
    OPENROUTER_DEFAULT_TTS,
    OPENROUTER_DEFAULT_VOICE,
    CloudProfile,
    load_profile,
)


def test_job_entrypoint_is_importable_and_picklable() -> None:
    assert entrypoint.__qualname__ == "entrypoint"
    assert pickle.loads(pickle.dumps(entrypoint)) is entrypoint


def test_entrypoint_uses_tts_only_greeting_without_an_llm_request() -> None:
    session = SimpleNamespace(
        start=AsyncMock(), say=AsyncMock(), generate_reply=Mock()
    )
    with (
        patch("forge_conversationalist.agent.load_profile", return_value=CloudProfile()),
        patch("forge_conversationalist.agent.load_integrations", return_value=None),
        patch("forge_conversationalist.agent.build_voice_session", return_value=session),
        patch("forge_conversationalist.agent.build_assistant_tools", return_value=[]),
    ):
        asyncio.run(entrypoint(SimpleNamespace(room="room", job=SimpleNamespace(metadata=""))))

    session.start.assert_awaited_once()
    session.say.assert_awaited_once_with("Hello! I'm here to help.")
    session.generate_reply.assert_not_called()


def test_job_voice_metadata_is_allowlisted_and_only_applies_to_qwen_openrouter() -> None:
    assert _requested_qwen_voice('{"tts_voice":"longanhuan_v3.6"}') == "longanhuan_v3.6"
    assert _requested_qwen_voice('{"tts_voice":"untrusted-value"}') is None
    assert _requested_qwen_voice('{"tts_voice":[]}') is None
    assert _requested_qwen_voice("not json") is None

    openrouter = load_profile({"CONVERSATIONALIST_PRESET": "openrouter"})
    chosen = _profile_for_job(openrouter, '{"tts_voice":"longanhuan_v3.6"}')
    assert chosen.tts_voice == "longanhuan_v3.6"

    other_model = CloudProfile(
        tts_provider="openrouter",
        tts_model="other/provider-model",
        tts_voice=OPENROUTER_DEFAULT_VOICE,
    )
    assert _profile_for_job(other_model, '{"tts_voice":"longanhuan_v3.6"}') is other_model
    livekit_tts = CloudProfile(tts_voice="provider/default")
    assert _profile_for_job(livekit_tts, '{"tts_voice":"longanhuan_v3.6"}') is livekit_tts
    assert openrouter.tts_model == OPENROUTER_DEFAULT_TTS


def test_job_vad_sensitivity_accepts_only_named_presets() -> None:
    assert _requested_vad_sensitivity('{"vad_sensitivity":"less"}') == "less"
    assert _requested_vad_sensitivity('{"vad_sensitivity":"more"}') == "more"
    assert _requested_vad_sensitivity('{"vad_sensitivity":[]}') == "standard"
    assert _requested_vad_sensitivity('{"vad_sensitivity":"unknown"}') == "standard"
    assert _requested_vad_sensitivity("not json") == "standard"


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
    assert session["turn_handling"] == {"turn_detection": "detector"}


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
