"""Validated, provider-specific settings for the first LiveKit Cloud profile.

Model identifiers remain external configuration. This module never loads
credentials or enables a local LLM, MCP tool, or robot.
"""

from __future__ import annotations

from dataclasses import dataclass
import os

AGENT_NAME = "forge-conversationalist"
DEFAULT_STT = "assemblyai/universal-3-6-pro"
DEFAULT_LLM = "google/gemma-4-31b-it"
DEFAULT_TTS = "fishaudio/s2.1-pro"
DEFAULT_VOICE = "fa4c9eb3dccc4806b382b40d61c6b10a"


@dataclass(frozen=True)
class CloudProfile:
    stt_model: str = DEFAULT_STT
    stt_language: str = "en"
    llm_model: str = DEFAULT_LLM
    tts_model: str = DEFAULT_TTS
    tts_voice: str = DEFAULT_VOICE

    def __post_init__(self) -> None:
        for field_name in ("stt_model", "llm_model", "tts_model"):
            model = getattr(self, field_name)
            if not model or model.strip() != model or "/" not in model:
                raise ValueError(f"{field_name} must be a LiveKit Inference model identifier")
        if not self.stt_language or not self.stt_language.strip():
            raise ValueError("stt_language must be set")
        if not self.tts_voice or not self.tts_voice.strip():
            raise ValueError("tts_voice must be set")


def load_profile(environ: dict[str, str] | None = None) -> CloudProfile:
    e = environ if environ is not None else os.environ
    return CloudProfile(
        stt_model=e.get("CONVERSATIONALIST_STT_MODEL", DEFAULT_STT),
        stt_language=e.get("CONVERSATIONALIST_STT_LANGUAGE", "en"),
        llm_model=e.get("CONVERSATIONALIST_LLM_MODEL", DEFAULT_LLM),
        tts_model=e.get("CONVERSATIONALIST_TTS_MODEL", DEFAULT_TTS),
        tts_voice=e.get("CONVERSATIONALIST_TTS_VOICE", DEFAULT_VOICE),
    )
