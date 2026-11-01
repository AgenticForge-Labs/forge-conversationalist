"""Offline tests check model request assembly without running audio or cloud services."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from forge_conversationalist.agent import build_voice_session
from forge_conversationalist.config import CloudProfile


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
