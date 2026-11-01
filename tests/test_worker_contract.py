"""Offline tests check model request assembly without running audio or cloud services."""

import asyncio
import pickle
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from forge_conversationalist.agent import build_voice_session, entrypoint
from forge_conversationalist.config import CloudProfile


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
        asyncio.run(entrypoint(SimpleNamespace(room="room")))

    session.start.assert_awaited_once()
    session.say.assert_awaited_once_with("Hello! I'm here to help.")
    session.generate_reply.assert_not_called()


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
