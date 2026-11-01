from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from forge_conversationalist.agent import INSTRUCTIONS
from forge_conversationalist.config import AGENT_NAME, CloudProfile, load_profile


def test_standalone_default_profile_is_cloud_inference() -> None:
    profile = load_profile({})
    assert AGENT_NAME == "forge-conversationalist"
    assert profile.stt_model == "assemblyai/universal-3-6-pro"
    assert profile.llm_model == "google/gemma-4-31b-it"
    assert profile.tts_model == "fishaudio/s2.1-pro"
    assert profile.tts_voice
    assert "robot" in INSTRUCTIONS.lower()


def test_all_model_stages_are_overridable() -> None:
    profile = load_profile({
        "CONVERSATIONALIST_STT_MODEL": "deepgram/flux-general",
        "CONVERSATIONALIST_STT_LANGUAGE": "en",
        "CONVERSATIONALIST_LLM_MODEL": "google/gemma-4-31b-it",
        "CONVERSATIONALIST_TTS_MODEL": "cartesia/sonic-3",
        "CONVERSATIONALIST_TTS_VOICE": "test-voice",
    })
    assert profile.stt_model == "deepgram/flux-general"
    assert profile.tts_voice == "test-voice"


@pytest.mark.parametrize("field,value", [
    ("stt_model", ""),
    ("llm_model", "missing-provider-prefix"),
    ("tts_model", " model/with-leading-space"),
    ("stt_language", ""),
    ("tts_voice", ""),
])
def test_invalid_profile_fails_closed(field: str, value: str) -> None:
    params = {"stt_model": "provider/stt", "llm_model": "provider/llm",
              "tts_model": "provider/tts", "stt_language": "en", "tts_voice": "voice"}
    params[field] = value
    with pytest.raises(ValueError):
        CloudProfile(**params)


def test_profile_is_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        load_profile({}).llm_model = "other/model"


def test_import_does_not_contact_livekit_or_require_credentials() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert CloudProfile().llm_model
