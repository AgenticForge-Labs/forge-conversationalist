"""Validated cloud speech and language-model profile configuration.

Only the realtime transport is always LiveKit. STT, LLM and TTS can
independently use LiveKit Inference or OpenRouter without changing rooms,
agent state, or tool permissions. API credentials are never profile fields.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

AGENT_NAME = "forge-conversationalist"
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"

Provider = Literal["livekit", "openrouter"]
Preset = Literal["livekit", "openrouter", "hybrid"]

DEFAULT_STT = "assemblyai/universal-3-6-pro"
DEFAULT_LLM = "google/gemma-4-31b-it"
DEFAULT_TTS = "fishaudio/s2.1-pro"
DEFAULT_VOICE = "fa4c9eb3dccc4806b382b40d61c6b10a"

OPENROUTER_DEFAULT_STT = "nvidia/parakeet-tdt-0.6b-v3"
OPENROUTER_DEFAULT_LLM = "qwen/qwen3.5-9b"
OPENROUTER_DEFAULT_TTS = "qwen/qwen-audio-3.0-tts-flash"
# From OpenRouter's published supported_voices model catalog as of Nov 2026.
# This is not Reachy's Aiden voice.
OPENROUTER_DEFAULT_VOICE = "loongjohn"
# Keep these aligned with OpenRouter's supported_voices for the configured Qwen TTS model.
OPENROUTER_TTS_VOICES = frozenset(("loongjohn", "longanhuan_v3.6"))

_PROVIDERS = frozenset({"livekit", "openrouter"})
_PRESETS: dict[str, tuple[Provider, Provider, Provider]] = {
    "livekit": ("livekit", "livekit", "livekit"),
    "openrouter": ("openrouter", "openrouter", "openrouter"),
    "hybrid": ("livekit", "openrouter", "livekit"),
}
_DEFAULTS = {
    "stt": {"livekit": DEFAULT_STT, "openrouter": OPENROUTER_DEFAULT_STT},
    "llm": {"livekit": DEFAULT_LLM, "openrouter": OPENROUTER_DEFAULT_LLM},
    "tts": {"livekit": DEFAULT_TTS, "openrouter": OPENROUTER_DEFAULT_TTS},
}
_VOICES = {"livekit": DEFAULT_VOICE, "openrouter": OPENROUTER_DEFAULT_VOICE}


@dataclass(frozen=True)
class CloudProfile:
    stt_model: str = DEFAULT_STT
    stt_language: str = "en"
    llm_model: str = DEFAULT_LLM
    tts_model: str = DEFAULT_TTS
    tts_voice: str = DEFAULT_VOICE
    stt_provider: Provider = "livekit"
    llm_provider: Provider = "livekit"
    tts_provider: Provider = "livekit"
    tts_response_format: str = "mp3"

    def __post_init__(self) -> None:
        for stage in ("stt", "llm", "tts"):
            provider = getattr(self, f"{stage}_provider")
            if provider not in _PROVIDERS:
                raise ValueError(f"{stage}_provider must be livekit or openrouter")
            model = getattr(self, f"{stage}_model")
            if not model or not isinstance(model, str) or model.strip() != model or "/" not in model:
                raise ValueError(f"{stage}_model requires a provider-qualified model ID")
        if not self.stt_language or not self.stt_language.strip():
            raise ValueError("stt_language must be set")
        if not self.tts_voice or not self.tts_voice.strip():
            raise ValueError("tts_voice must be set")
        if self.tts_response_format not in {"mp3", "pcm", "wav"}:
            raise ValueError("tts_response_format must be mp3, pcm, or wav")

    @property
    def requires_openrouter(self) -> bool:
        return "openrouter" in (self.stt_provider, self.llm_provider, self.tts_provider)


def load_profile(environ: dict[str, str] | None = None) -> CloudProfile:
    e = environ if environ is not None else os.environ
    preset = e.get("CONVERSATIONALIST_PRESET", "livekit")
    if preset not in _PRESETS:
        raise ValueError("CONVERSATIONALIST_PRESET must be livekit, openrouter, or hybrid")

    providers = {
        stage: e.get(f"CONVERSATIONALIST_{stage.upper()}_PROVIDER", default)
        for stage, default in zip(("stt", "llm", "tts"), _PRESETS[preset], strict=True)
    }
    for stage, provider in providers.items():
        if provider not in _PROVIDERS:
            raise ValueError(f"CONVERSATIONALIST_{stage.upper()}_PROVIDER is invalid")

    return CloudProfile(
        stt_provider=providers["stt"],
        llm_provider=providers["llm"],
        tts_provider=providers["tts"],
        stt_model=e.get(
            "CONVERSATIONALIST_STT_MODEL", _DEFAULTS["stt"][providers["stt"]]
        ),
        stt_language=e.get("CONVERSATIONALIST_STT_LANGUAGE", "en"),
        llm_model=e.get(
            "CONVERSATIONALIST_LLM_MODEL", _DEFAULTS["llm"][providers["llm"]]
        ),
        tts_model=e.get(
            "CONVERSATIONALIST_TTS_MODEL", _DEFAULTS["tts"][providers["tts"]]
        ),
        tts_voice=e.get("CONVERSATIONALIST_TTS_VOICE", _VOICES[providers["tts"]]),
        tts_response_format=e.get("CONVERSATIONALIST_TTS_RESPONSE_FORMAT", "mp3"),
    )
