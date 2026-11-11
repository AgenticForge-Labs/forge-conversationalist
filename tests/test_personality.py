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
    assert "begin your reply with a tag [gesture: NAME]" in instructions
    assert "you are the robot" in instructions
    assert "Never say you are doing or did a gesture unless you tagged it or called the tool" in instructions


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


def test_self_description_is_background_that_is_never_volunteered():
    from forge_conversationalist.agent import _self_description
    from forge_conversationalist.config import CloudProfile

    profile = CloudProfile(llm_model="openai/gpt-test", tts_model="vendor/voice-test")
    text = _self_description(profile, ("soarm101", "reachy"))
    assert "openai/gpt-test" in text and "Feetech STS3215" in text and "Reachy Mini" in text
    assert "StackChan" not in text, "only connected bodies are described"
    assert "Never mention, recite or hint at it unless the user asks" in text
    assert "do not know rather than guessing" in text
    integrations = type("Integrations", (), {"ensemble": object()})()
    assert text in _room_instructions(
        integrations, caller_id="p", session_id="r", self_description=text)
    assert "Feetech" not in _room_instructions(integrations, caller_id="p", session_id="r")
