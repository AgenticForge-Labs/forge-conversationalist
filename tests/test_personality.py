import pytest

from forge_conversationalist.agent import _room_instructions
from forge_conversationalist.config import load_profile
from forge_conversationalist.integrations import Integrations
from forge_conversationalist.personality import DEFAULT, FOUR_G, load_personality


def test_operator_selects_personality_without_granting_integrations():
    assert load_personality({}) == DEFAULT
    assert load_personality({"CONVERSATIONALIST_PERSONALITY": "4G"}) == FOUR_G
    with pytest.raises(ValueError):
        load_personality({"CONVERSATIONALIST_PERSONALITY": "unknown"})
    instructions = _room_instructions(
        Integrations(), caller_id=None, session_id=None, personality=FOUR_G,
    )
    assert "You are 4G" in instructions
    assert "You are Forge Conversationalist" not in instructions
    assert "Use only tools actually available" in instructions
    assert "Voice is not identity authentication" in instructions
    assert "business services and ready" not in FOUR_G.greeting


def test_4g_retains_ensemble_gesture_guard():
    integrations = type("Integrations", (), {"ensemble": object()})()
    instructions = _room_instructions(
        integrations, caller_id="person", session_id="room", personality=FOUR_G,
    )
    assert "You are 4G" in instructions
    assert "When the user asks you to do something your body can do" in instructions
    assert "you are the robot" in instructions
    assert "never say you are doing or did a gesture unless you called the tool" in instructions


def test_4g_defaults_to_heygen_lucian_without_changing_stt_or_llm():
    profile = load_profile({"CONVERSATIONALIST_PERSONALITY": "4g"})
    assert (profile.tts_provider, profile.tts_model, profile.tts_voice) == (
        "openrouter", "heygen/heygen-voice-1-turbo", "lucian",
    )
    assert profile.tts_response_format == "pcm"
    assert (profile.stt_provider, profile.llm_provider) == ("livekit", "livekit")
    explicit = load_profile({
        "CONVERSATIONALIST_PERSONALITY": "4g",
        "CONVERSATIONALIST_TTS_PROVIDER": "livekit",
    })
    assert explicit.tts_provider == "livekit"
