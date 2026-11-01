"""Standalone, cloud-inference LiveKit voice worker.

LiveKit remains the only realtime media/room/turn engine. Existing LiveKit
provider plugins own OpenRouter API request assembly; no duplicate audio
transport, model client, agent memory, MCP gateway or robot is created here.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace

from .assistant_tools import build_assistant_tools
from .config import (
    AGENT_NAME,
    OPENROUTER_API_BASE,
    OPENROUTER_DEFAULT_TTS,
    OPENROUTER_TTS_VOICES,
    VAD_SENSITIVITY_THRESHOLDS,
    CloudProfile,
    load_profile,
)
from .integrations import load_integrations

INSTRUCTIONS = (
    "You are Forge Conversationalist, a helpful spoken assistant. "
    "Answer clearly, accurately, and briefly by default. "
    "Use normal conversational speech rather than Markdown or long lists. "
    "Never claim to have external tools, memory, robot control, or private "
    "business information when those integrations are unavailable. "
    "Use only tools actually available in this room. Never imply an MCP or "
    "robot action was successful without a returned confirmed outcome. "
    "Hermes delegation starts an asynchronous run: acceptance is not completion. "
    "Offer to check the actual run status when the user asks about progress. "
    "Physical motion and consequential changes require the gateway's own "
    "permission, validation, and human approval."
)


def build_voice_session(
    profile: CloudProfile,
    *,
    vad_sensitivity: str = "standard",
    sdk: object | None = None,
    openai_plugin: object | None = None,
    openrouter_key: str | None = None,
) -> object:
    """Assemble a LiveKit AgentSession from independent provider selections.

    Testing can inject SDK constructors. A missing OpenRouter key fails
    before creating any cloud client or audio model. Provider endpoints are
    fixed by the application, not by an untrusted browser participant.
    """
    api_key = openrouter_key if openrouter_key is not None else os.getenv("OPENROUTER_API_KEY")
    if profile.requires_openrouter and not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required for selected OpenRouter stages")

    if sdk is None:
        from livekit import agents as sdk
    if profile.requires_openrouter and openai_plugin is None:
        from livekit.plugins import openai as openai_plugin

    if vad_sensitivity not in VAD_SENSITIVITY_THRESHOLDS:
        raise ValueError("vad_sensitivity must be less, standard, or more")

    from livekit.plugins import silero

    local_turn_model = os.getenv("CONVERSATIONALIST_TURN_DETECTOR_VERSION") == "v1-mini"

    stt = (
        sdk.inference.STT(model=profile.stt_model, language=profile.stt_language)
        if profile.stt_provider == "livekit"
        else openai_plugin.STT(
            model=profile.stt_model,
            language=profile.stt_language,
            base_url=OPENROUTER_API_BASE,
            api_key=api_key,
            use_realtime=False,  # OpenRouter offers buffered transcription, not WS STT
        )
    )
    llm = (
        sdk.inference.LLM(model=profile.llm_model)
        if profile.llm_provider == "livekit"
        else openai_plugin.LLM.with_openrouter(
            model=profile.llm_model,
            api_key=api_key,
            base_url=OPENROUTER_API_BASE,
        )
    )
    tts = (
        sdk.inference.TTS(model=profile.tts_model, voice=profile.tts_voice)
        if profile.tts_provider == "livekit"
        else openai_plugin.TTS(
            model=profile.tts_model,
            voice=profile.tts_voice,
            base_url=OPENROUTER_API_BASE,
            api_key=api_key,
            response_format=profile.tts_response_format,
        )
    )
    turn_handling = {
        "turn_detection": _build_turn_detector(sdk),
    }
    if local_turn_model:
        # Adaptive interruption uses a separate LiveKit cloud endpoint.
        # Keep local runs fully on-device with the standard VAD strategy.
        turn_handling["interruption"] = {"mode": "vad"}

    threshold = VAD_SENSITIVITY_THRESHOLDS[vad_sensitivity]
    vad = silero.VAD.load(
        **({"activation_threshold": threshold} if threshold is not None else {})
    )

    return sdk.AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        vad=vad,
        turn_handling=sdk.TurnHandlingOptions(**turn_handling),
    )


def _build_turn_detector(sdk: object) -> object:
    """Build LiveKit's default detector or an explicitly selected version."""
    version = os.getenv("CONVERSATIONALIST_TURN_DETECTOR_VERSION")
    if version is None:
        return sdk.inference.TurnDetector()
    if version not in {"v1", "v1-mini"}:
        raise ValueError("CONVERSATIONALIST_TURN_DETECTOR_VERSION must be v1 or v1-mini")
    return sdk.inference.TurnDetector(version=version)


async def entrypoint(ctx: object) -> None:
    """Create a room session from worker environment.

    This must stay module-level: LiveKit's Linux process executor pickles
    the entrypoint before dispatching a job to its worker process.
    """
    from livekit.agents import Agent

    metadata = getattr(ctx.job, "metadata", "")
    profile = _profile_for_job(load_profile(), metadata)
    vad_sensitivity = _requested_vad_sensitivity(metadata)
    integrations = load_integrations()
    session = build_voice_session(profile, vad_sensitivity=vad_sensitivity)
    agent = Agent(
        instructions=INSTRUCTIONS,
        tools=build_assistant_tools(integrations),
    )
    await session.start(room=ctx.room, agent=agent)
    # A fixed greeting needs TTS only. Asking the LLM to generate it with only
    # system instructions fails on providers that require a user query and
    # creates an unnecessary model request.
    await session.say("Hello! I'm here to help.")


def _requested_qwen_voice(metadata: str) -> str | None:
    """Read only an allowlisted voice from server-authored job metadata."""
    if not metadata:
        return None
    try:
        request = json.loads(metadata)
    except (TypeError, json.JSONDecodeError):
        return None
    voice = request.get("tts_voice") if isinstance(request, dict) else None
    return voice if isinstance(voice, str) and voice in OPENROUTER_TTS_VOICES else None


def _profile_for_job(profile: CloudProfile, metadata: str) -> CloudProfile:
    """Apply a requested voice only to the OpenRouter Qwen TTS profile."""
    voice = _requested_qwen_voice(metadata)
    if (
        voice is not None
        and profile.tts_provider == "openrouter"
        and profile.tts_model == OPENROUTER_DEFAULT_TTS
    ):
        return replace(profile, tts_voice=voice)
    return profile


def _requested_vad_sensitivity(metadata: str) -> str:
    """Read only a supported VAD preset from server-authored job metadata."""
    if not metadata:
        return "standard"
    try:
        request = json.loads(metadata)
    except (TypeError, json.JSONDecodeError):
        return "standard"
    sensitivity = request.get("vad_sensitivity") if isinstance(request, dict) else None
    return (
        sensitivity
        if isinstance(sensitivity, str) and sensitivity in VAD_SENSITIVITY_THRESHOLDS
        else "standard"
    )


def main() -> None:
    from dotenv import load_dotenv
    from livekit import agents
    from livekit.agents import AgentServer

    load_dotenv(".env.local")
    profile = load_profile()
    load_integrations()
    if profile.requires_openrouter and not os.getenv("OPENROUTER_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY is required for selected OpenRouter stages")
    server = AgentServer()

    server.rtc_session(agent_name=AGENT_NAME)(entrypoint)

    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
