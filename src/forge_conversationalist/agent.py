"""Standalone, cloud-inference LiveKit voice worker.

LiveKit remains the only realtime media/room/turn engine. Existing LiveKit
provider plugins own OpenRouter API request assembly; no duplicate audio
transport, model client, agent memory, MCP gateway or robot is created here.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from typing import Any

from .assistant_tools import build_assistant_tools
from .config import (
    AGENT_NAME,
    OPENROUTER_API_BASE,
    OPENROUTER_DEFAULT_TTS,
    OPENROUTER_LEGACY_QWEN_TTS,
    OPENROUTER_LEGACY_QWEN_VOICES,
    VAD_SENSITIVITY_THRESHOLDS,
    CloudProfile,
    load_profile,
)
from .integrations import load_integrations
from .embodiment import EmbodimentSession
from .speech_catalog import selected_speech_model
from .text_hook import install_assistant_text_hook, install_session_state_hook
from .transcript_rpc import register_transcript_rpc

logger = logging.getLogger(__name__)

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
    "permission, validation, and human approval. When a restricted Reachy move "
    "tool is available, call it only after the user explicitly asks for a "
    "movement or expression. Never move Reachy automatically while listening, "
    "speaking, idle, greeting, or acknowledging. Use only the exact approved "
    "moves enforced by the gateway."
)


def build_voice_session(
    profile: CloudProfile,
    *,
    vad_sensitivity: str = "standard",
    tts_sample_rate: int = 24000,
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
    tts = _build_tts(profile, api_key=api_key, openai_plugin=openai_plugin,
                     tts_sample_rate=tts_sample_rate, sdk=sdk)
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


def _build_tts(
    profile: CloudProfile,
    *,
    api_key: str | None = None,
    openai_plugin: object | None = None,
    tts_sample_rate: int = 24000,
    sdk: object | None = None,
) -> object:
    """Build the selected provider TTS for startup or a live voice swap."""
    if profile.tts_provider == "livekit":
        if sdk is None:
            from livekit import agents as sdk
        return sdk.inference.TTS(model=profile.tts_model, voice=profile.tts_voice)
    key = api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is required for selected OpenRouter stages")
    if openai_plugin is None:
        from livekit.plugins import openai as openai_plugin
    kwargs = {
        "model": profile.tts_model,
        "voice": profile.tts_voice,
        "base_url": OPENROUTER_API_BASE,
        "api_key": key,
        "response_format": profile.tts_response_format,
    }
    if tts_sample_rate != 24000:
        kwargs["sample_rate"] = tts_sample_rate
    return openai_plugin.TTS(**kwargs)


def _build_turn_detector(sdk: object) -> object:
    """Build LiveKit's default detector or an explicitly selected version."""
    version = os.getenv("CONVERSATIONALIST_TURN_DETECTOR_VERSION")
    if version is None:
        return sdk.inference.TurnDetector()
    if version not in {"v1", "v1-mini"}:
        raise ValueError("CONVERSATIONALIST_TURN_DETECTOR_VERSION must be v1 or v1-mini")
    return sdk.inference.TurnDetector(version=version)


def _default_tts_sample_rate(profile: CloudProfile) -> int:
    """Use HeyGen Turbo's documented rate without a startup catalog request."""
    return 44100 if (
        profile.tts_provider == "openrouter"
        and profile.tts_model == OPENROUTER_DEFAULT_TTS
    ) else 24000


async def entrypoint(ctx: object) -> None:
    """Create a room session from worker environment.

    This must stay module-level: LiveKit's Linux process executor pickles
    the entrypoint before dispatching a job to its worker process.
    """
    from livekit.agents import Agent, room_io

    metadata = getattr(ctx.job, "metadata", "")
    profile = load_profile()
    requested_model = _requested_speech_model(metadata)
    selected_model = None
    if requested_model is not None:
        selected_model = selected_speech_model(requested_model[0], requested_model[1])
        if selected_model is None:
            raise ValueError("Requested speech model or voice is not in the current OpenRouter catalog")
        profile = replace(
            profile,
            tts_provider="openrouter",
            tts_model=selected_model["id"],
            tts_voice=requested_model[1],
            tts_response_format=selected_model["responseFormat"],
        )
    else:
        profile = _profile_for_job(profile, metadata)
    vad_sensitivity = _requested_vad_sensitivity(metadata)
    integrations = load_integrations()
    authorized_identity = _authorized_identity(metadata)
    room_name = getattr(ctx.room, "name", None)
    agent_integrations = integrations
    if integrations.puppeteer_stdio is not None and (
        not authorized_identity or not isinstance(room_name, str) or not room_name
    ):
        # Do not expose action tools if the signed room dispatch lacks stable IDs.
        agent_integrations = replace(integrations, puppeteer_stdio=None)
    session = build_voice_session(
        profile,
        vad_sensitivity=vad_sensitivity,
        tts_sample_rate=selected_model["sampleRate"] if selected_model else _default_tts_sample_rate(profile),
    )
    install_assistant_text_hook(
        session,
        lambda signal, _text: logger.info(
            "assistant text completed",
            extra={"item_id": signal.item_id, "interrupted": signal.interrupted},
        ),
    )
    embodiment = None
    if agent_integrations.embodiment is not None:
        if not isinstance(room_name, str) or not room_name:
            raise RuntimeError("Embodiment requires a stable LiveKit room identity")
        embodiment = EmbodimentSession(
            agent_integrations.embodiment,
            room_id=room_name,
            caller_id=authorized_identity or "conversationalist-worker",
        )
        await embodiment.start()  # Fake-only discovery and MCP handshake; no SDK device.
        ctx.add_shutdown_callback(embodiment.close)

    def handle_state(signal):
        logger.debug(
            "conversation state changed", extra={"state": signal.state, "source": signal.source}
        )
        if embodiment is not None:
            embodiment.observe(signal.state)

    install_session_state_hook(session, handle_state)
    agent = Agent(
        instructions=_room_instructions(
            agent_integrations, caller_id=authorized_identity, session_id=room_name
        ),
        tools=build_assistant_tools(
            agent_integrations,
            embodiment_session=embodiment if authorized_identity else None,
        ),
    )
    room_options = room_io.RoomOptions(
        close_on_disconnect=False,
        **({"participant_identity": authorized_identity} if authorized_identity else {}),
    )
    # RoomIO resolves the explicitly linked participant while starting. The
    # room must already be connected before that resolution in Agents 1.8.
    await ctx.connect()
    await session.start(room=ctx.room, agent=agent, room_options=room_options)
    if authorized_identity:
        register_transcript_rpc(
            ctx.room.local_participant,
            history=session.history,
            authorized_identity=authorized_identity,
            room=ctx.room,
        )
        _register_voice_rpc(
            ctx.room.local_participant,
            caller_identity=authorized_identity,
            agent=agent,
            base_profile=load_profile(),
            current_profile=profile,
        )
    # A fixed greeting needs TTS only. Asking the LLM to generate it with only
    # system instructions fails on providers that require a user query and
    # creates an unnecessary model request.
    await session.say("Hello! I'm here to help.")


def _room_instructions(
    integrations: object,
    *,
    caller_id: str | None,
    session_id: str | None,
) -> str:
    """Add only the operator-pinned Reachy move IDs to this room's guidance."""
    config = getattr(integrations, "puppeteer_stdio", None)
    if config is None:
        if getattr(integrations, "embodiment", None) is not None:
            return INSTRUCTIONS + (
                " A fake-only Puppeteer embodiment is configured. Use "
                "discover_performer to list its exact simulated expression names "
                "and revisions, and request_performer_expression only when a "
                "person explicitly requests expression. This is simulation, not "
                "proof of physical robot motion. Check accepted action IDs with "
                "check_performer_action; do not retry unknown outcomes. "
                "Speaking/listening state is approximate, not a confirmed audio "
                "playback clock."
            )
        return INSTRUCTIONS
    pins = ", ".join(f"{asset_id} ({revision})" for asset_id, revision in config.approved_moves)
    available = set(config.allowed_tools)
    guidance = (
        " The Puppeteer Reachy tool is available for explicit user-requested "
        "expressions or movements only. Call list_assets to retrieve the "
        "gateway-filtered approved moves. Approved move pins are: " + pins + ". "
        "Call play_move only with one exact listed asset_id and revision. "
        "Never use a move to greet, acknowledge, listen, speak, or idle. "
        f"For play_move, use caller_id={caller_id} and session_id={session_id}. "
        "Generate one unique action_id per requested move. Do not retry an "
        "ambiguous or timed-out move; if action_lookup is available, check "
        "that same action_id before saying whether it completed."
    )
    if "device_stop" in available:
        guidance += " Call device_stop only when the user explicitly asks to stop Reachy."
    return INSTRUCTIONS + guidance


def _authorized_identity(metadata: str) -> str | None:
    if not metadata:
        return None
    try:
        request = json.loads(metadata)
    except (TypeError, json.JSONDecodeError):
        return None
    identity = request.get("authorized_identity") if isinstance(request, dict) else None
    return identity if isinstance(identity, str) and identity else None


def _register_voice_rpc(
    local_participant: object,
    *,
    caller_identity: str,
    agent: object,
    base_profile: CloudProfile,
    current_profile: CloudProfile | None = None,
) -> None:
    """Expose speech read and switching only to this room's authorized user."""
    from livekit import rtc

    active_profile = current_profile or base_profile

    async def get_speech(data: object) -> str:
        if getattr(data, "caller_identity", None) != caller_identity:
            raise rtc.RpcError(
                code=rtc.RpcError.ErrorCode.APPLICATION_ERROR,
                message="Only the authorized room participant can read speech settings",
            )
        return json.dumps({"model": active_profile.tts_model, "voice": active_profile.tts_voice})

    async def change_voice(data: object) -> str:
        nonlocal active_profile
        if getattr(data, "caller_identity", None) != caller_identity:
            raise rtc.RpcError(
                code=rtc.RpcError.ErrorCode.APPLICATION_ERROR,
                message="Only the authorized room participant can change speech settings",
            )
        try:
            requested = json.loads(data.payload)
        except (TypeError, json.JSONDecodeError):
            raise rtc.RpcError(code=rtc.RpcError.ErrorCode.APPLICATION_ERROR, message="Invalid settings")
        if not isinstance(requested, dict):
            raise rtc.RpcError(code=rtc.RpcError.ErrorCode.APPLICATION_ERROR, message="Invalid settings")

        model_id = requested.get("tts_model")
        voice = requested.get("tts_voice")
        sample_rate = _default_tts_sample_rate(base_profile)
        if model_id is None and voice is None:
            profile = base_profile
        else:
            selected = selected_speech_model(model_id, voice)
            if selected is None:
                raise rtc.RpcError(code=rtc.RpcError.ErrorCode.APPLICATION_ERROR, message="Unsupported speech model or voice")
            profile = replace(
                base_profile,
                tts_provider="openrouter",
                tts_model=selected["id"],
                tts_voice=voice,
                tts_response_format=selected["responseFormat"],
            )
            sample_rate = selected["sampleRate"]
        tts = _build_tts(profile, tts_sample_rate=sample_rate)
        agent.update_options(tts=tts)
        active_profile = profile
        return json.dumps({"model": profile.tts_model, "voice": profile.tts_voice})

    local_participant.register_rpc_method("forge.get_speech", get_speech)
    local_participant.register_rpc_method("forge.change_speech", change_voice)


def _requested_speech_model(metadata: str) -> tuple[Any, Any] | None:
    """Read the per-session TTS choice; full allowlist validation is separate."""
    if not metadata:
        return None
    try:
        request = json.loads(metadata)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(request, dict) or "tts_model" not in request:
        return None
    return request.get("tts_model"), request.get("tts_voice")


def _requested_qwen_voice(metadata: str) -> str | None:
    """Read only an allowlisted voice from server-authored job metadata."""
    if not metadata:
        return None
    try:
        request = json.loads(metadata)
    except (TypeError, json.JSONDecodeError):
        return None
    voice = request.get("tts_voice") if isinstance(request, dict) else None
    return voice if isinstance(voice, str) and voice in OPENROUTER_LEGACY_QWEN_VOICES else None


def _profile_for_job(profile: CloudProfile, metadata: str) -> CloudProfile:
    """Apply legacy Qwen voice metadata only to its original model."""
    voice = _requested_qwen_voice(metadata)
    if (
        voice is not None
        and profile.tts_provider == "openrouter"
        and profile.tts_model == OPENROUTER_LEGACY_QWEN_TTS
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
