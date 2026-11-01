"""Deterministic contract tests for independent LiveKit / OpenRouter selection."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from forge_conversationalist.agent import build_voice_session
from forge_conversationalist.config import (
    OPENROUTER_API_BASE,
    OPENROUTER_DEFAULT_STT,
    OPENROUTER_DEFAULT_TTS,
    OPENROUTER_DEFAULT_VOICE,
    CloudProfile,
    load_profile,
)


def _fake_adapters():
    inference = SimpleNamespace(
        STT=MagicMock(side_effect=lambda **kw: ("livekit-stt", kw)),
        LLM=MagicMock(side_effect=lambda **kw: ("livekit-llm", kw)),
        TTS=MagicMock(side_effect=lambda **kw: ("livekit-tts", kw)),
        TurnDetector=MagicMock(return_value="turn-detector"),
    )
    sdk = SimpleNamespace(
        inference=inference,
        AgentSession=MagicMock(side_effect=lambda **kw: kw),
        TurnHandlingOptions=MagicMock(side_effect=lambda **kw: kw),
    )
    openai = SimpleNamespace(
        STT=MagicMock(side_effect=lambda **kw: ("openrouter-stt", kw)),
        LLM=SimpleNamespace(
            with_openrouter=MagicMock(side_effect=lambda **kw: ("openrouter-llm", kw))
        ),
        TTS=MagicMock(side_effect=lambda **kw: ("openrouter-tts", kw)),
    )
    silero = SimpleNamespace(VAD=SimpleNamespace(load=MagicMock(return_value="silero")))
    return sdk, openai, silero


def _build(profile: CloudProfile, key: str | None = "test-key"):
    sdk, openai, silero = _fake_adapters()
    with patch.dict("sys.modules", {"livekit.plugins.silero": silero}):
        result = build_voice_session(
            profile, sdk=sdk, openai_plugin=openai, openrouter_key=key
        )
    return result, sdk, openai


def test_openrouter_profile_uses_confirmed_published_qwen_voice() -> None:
    profile = load_profile({"CONVERSATIONALIST_PRESET": "openrouter"})
    assert (profile.stt_provider, profile.llm_provider, profile.tts_provider) == (
        "openrouter", "openrouter", "openrouter"
    )
    assert profile.stt_model == OPENROUTER_DEFAULT_STT
    assert profile.tts_model == OPENROUTER_DEFAULT_TTS
    assert profile.tts_voice == OPENROUTER_DEFAULT_VOICE == "loongjohn"
    assert profile.requires_openrouter


def test_livekit_default_retains_original_models_and_no_new_key() -> None:
    profile = load_profile({})
    assert not profile.requires_openrouter
    result, sdk, openai = _build(profile, key=None)
    assert result["stt"][0] == "livekit-stt"
    assert result["llm"][0] == "livekit-llm"
    assert result["tts"][0] == "livekit-tts"
    assert sdk.inference.STT.call_count == 1
    assert not openai.STT.called
    assert not openai.TTS.called
    assert not openai.LLM.with_openrouter.called


def test_openrouter_stages_use_fixed_base_and_transcription_http() -> None:
    profile = load_profile({"CONVERSATIONALIST_PRESET": "openrouter"})
    result, sdk, openai = _build(profile)
    assert result["stt"] == (
        "openrouter-stt",
        {
            "model": OPENROUTER_DEFAULT_STT,
            "language": "en",
            "base_url": OPENROUTER_API_BASE,
            "api_key": "test-key",
            "use_realtime": False,
        },
    )
    assert result["llm"] == (
        "openrouter-llm",
        {
            "model": profile.llm_model,
            "base_url": OPENROUTER_API_BASE,
            "api_key": "test-key",
        },
    )
    assert result["tts"] == (
        "openrouter-tts",
        {
            "model": OPENROUTER_DEFAULT_TTS,
            "voice": "loongjohn",
            "base_url": OPENROUTER_API_BASE,
            "api_key": "test-key",
            "response_format": "mp3",
        },
    )
    assert result["vad"] == "silero"
    assert result["turn_handling"] == {"turn_detection": "turn-detector"}
    assert not sdk.inference.STT.called
    assert not sdk.inference.LLM.called
    assert not sdk.inference.TTS.called
    assert openai.STT.call_count == 1


def test_hybrid_selects_one_openrouter_stage() -> None:
    profile = load_profile({"CONVERSATIONALIST_PRESET": "hybrid"})
    result, sdk, openai = _build(profile)
    assert result["stt"][0] == "livekit-stt"
    assert result["llm"][0] == "openrouter-llm"
    assert result["tts"][0] == "livekit-tts"
    assert sdk.inference.STT.call_count == 1
    assert openai.LLM.with_openrouter.call_count == 1


def test_each_stage_can_be_overridden_without_model_mismatch() -> None:
    profile = load_profile({
        "CONVERSATIONALIST_PRESET": "hybrid",
        "CONVERSATIONALIST_STT_PROVIDER": "openrouter",
        "CONVERSATIONALIST_LLM_PROVIDER": "livekit",
        "CONVERSATIONALIST_TTS_PROVIDER": "openrouter",
        "CONVERSATIONALIST_TTS_MODEL": "elevenlabs/eleven-v4-turbo",
        "CONVERSATIONALIST_TTS_VOICE": "george",
        "CONVERSATIONALIST_TTS_RESPONSE_FORMAT": "wav",
    })
    result, _, _ = _build(profile)
    assert result["stt"][0] == "openrouter-stt"
    assert result["llm"] == ("livekit-llm", {"model": "google/gemma-4-31b-it"})
    assert result["tts"][1]["model"] == "elevenlabs/eleven-v4-turbo"
    assert result["tts"][1]["voice"] == "george"
    assert result["tts"][1]["response_format"] == "wav"


@pytest.mark.parametrize(
    "settings",
    [
        {"CONVERSATIONALIST_PRESET": "unknown"},
        {"CONVERSATIONALIST_LLM_PROVIDER": "untrusted"},
        {"CONVERSATIONALIST_TTS_MODEL": "invalid"},
        {"CONVERSATIONALIST_STT_MODEL": ""},
        {"CONVERSATIONALIST_TTS_RESPONSE_FORMAT": "flac"},
        {"CONVERSATIONALIST_TTS_VOICE": ""},
    ],
)
def test_invalid_model_provider_and_format_fail_closed(settings: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        load_profile(settings)


def test_openrouter_key_required_before_any_provider_calls() -> None:
    sdk, openai, silero = _fake_adapters()
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.dict("sys.modules", {"livekit.plugins.silero": silero}),
        pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"),
    ):
        build_voice_session(
            load_profile({"CONVERSATIONALIST_PRESET": "hybrid"}),
            sdk=sdk,
            openai_plugin=openai,
        )
    assert not sdk.AgentSession.called
    assert not sdk.inference.STT.called
    assert not openai.LLM.with_openrouter.called
    assert not silero.VAD.load.called


def test_openrouter_key_is_never_stored_in_configuration() -> None:
    profile = load_profile({"CONVERSATIONALIST_PRESET": "openrouter",
                            "OPENROUTER_API_KEY": "do-not-store"})
    assert "do-not-store" not in repr(profile)
    assert "openrouter_key" not in vars(profile)
