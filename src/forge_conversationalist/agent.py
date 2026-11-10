"""Standalone, cloud-inference LiveKit voice worker.

LiveKit remains the only realtime media/room/turn engine. Existing LiveKit
provider plugins own OpenRouter API request assembly; no duplicate audio
transport, model client, agent memory, MCP gateway or robot is created here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
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
from .embodiment import EmbodimentSession
from .ensemble import EnsembleSession
from .identified_playback import SEGMENT_ID_KEY, IdentifiedSpeechBridge
from .integrations import Integrations, load_integrations
from .mail_stream_notifications import AmbientMailNotifications, load_mail_stream_config
from .personality import DEFAULT, Personality, load_personality
from .playback_presence import PlaybackPresence
from .robot_rpc import register_robot_rpc
from .room_lifecycle import install_ensemble_room_shutdown_hooks
from .speech_catalog import selected_speech_model
from .speech_envelope_tap import agent_class_with_speech_envelope
from .text_hook import install_assistant_text_hook, install_session_state_hook
from .transcript_rpc import register_transcript_rpc

logger = logging.getLogger(__name__)


def _identified_output_sdk_available() -> bool:
    """Check for the optional SDK API; the live sink is checked separately."""
    try:
        from livekit.agents.types import USERDATA_PLAYOUT_SEGMENT_ID
        from livekit.agents.voice.io import AudioOutput
    except ImportError:
        return False
    return (USERDATA_PLAYOUT_SEGMENT_ID == SEGMENT_ID_KEY
            and hasattr(AudioOutput, "identified_playback_supported"))

INSTRUCTIONS = (
    "You are Forge Conversationalist, a helpful spoken assistant. "
    "Answer clearly, accurately, and briefly by default. "
    "Use normal conversational speech rather than Markdown or long lists. "
    "Never claim to have external tools, memory, robot control, or private "
    "business information when those integrations are unavailable. "
    "Use only tools actually available in this room. Never imply an MCP or "
    "robot action was successful without a returned confirmed outcome. "
    "Anything you cannot do yourself goes straight to Hermes without asking the "
    "user for permission first: the current date or time, live facts, web lookups, "
    "research, coding, and anything beyond trivial arithmetic. Never tell the user "
    "you lack access to the time or to live information; delegate it instead. "
    "Questions are independent: if one task is still running, delegate the next "
    "question immediately rather than waiting or asking the user to come back. "
    "Acknowledge a delegated task in at most four words (for example \"On it.\"); "
    "never read task identifiers "
    "aloud, and never claim an answer before it arrives. Each result is announced "
    "with its topic and kept for this conversation: when the user asks about one, "
    "use check_hermes_run with that topic instead of guessing or re-running it. "
    "Company email and calendar are read-only through Hermes company services; "
    "never claim to have sent mail or changed a calendar, because those writes "
    "are not available to you. "
    "Physical motion and consequential changes require the gateway's own "
    "permission, validation, and human approval. When a restricted Reachy move "
    "tool is available, call it only after the user explicitly asks for a "
    "movement or expression, and read the tool's spoken_guidance before "
    "replying: never say a gesture was requested when the outcome was "
    "unsupported or denied. "
    "movement or expression. Never move Reachy automatically while listening, "
    "speaking, idle, greeting, or acknowledging. Use only the exact approved "
    "moves enforced by the gateway."
)


def build_voice_session(
    profile: CloudProfile,
    *,
    vad_sensitivity: str = "standard",
    turn_pacing: str = "standard",
    interruption: dict[str, object] | None = None,
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
    if turn_pacing not in {"quick", "standard", "patient"}:
        raise ValueError("turn_pacing must be quick, standard, or patient")

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
    llm = _build_llm(profile, api_key=api_key, openai_plugin=openai_plugin, sdk=sdk)
    tts = _build_tts(profile, api_key=api_key, openai_plugin=openai_plugin,
                     tts_sample_rate=tts_sample_rate, sdk=sdk)
    turn_handling = {
        "turn_detection": _build_turn_detector(sdk),
    }
    # Speculative generation starts the reply before the user's turn is fully
    # committed, so the model's think time overlaps the end of their speech.
    # Measured LLM time-to-first-sentence is ~0.5-1.0 s, which is the amount
    # this can hide. Operators can still disable it explicitly.
    # LiveKit 1.8 enables speculative generation by default, so an A/B that
    # merely omits the key measures nothing. Set it explicitly both ways.
    preemptive_on = os.getenv(
        "CONVERSATIONALIST_PREEMPTIVE_GENERATION", "true"
    ).strip().lower() not in {"false", "0", "no"}
    # A/B on 2026-11-04: speculative TTS measured no better than the SDK
    # default (median commit->speaking 1032 ms with it, 816 ms without), so it
    # stays opt-in rather than paying for audio that may be discarded.
    preemptive_tts = os.getenv(
        "CONVERSATIONALIST_PREEMPTIVE_TTS", "false"
    ).strip().lower() not in {"false", "0", "no"}
    turn_handling["preemptive_generation"] = {
        "enabled": preemptive_on,
        "preemptive_tts": preemptive_tts and preemptive_on,
    }
    if turn_pacing != "standard":
        delay = 0.25 if turn_pacing == "quick" else 1.2
        turn_handling["endpointing"] = {"mode": "fixed", "min_delay": delay, "max_delay": delay}
    # Adaptive interruption is a remote LiveKit Cloud model with no local
    # fallback, unlike the turn detector's v1-mini. Keep local rooms on VAD
    # unless an operator explicitly asks for adaptive inference.
    interruption_mode = os.getenv(
        "CONVERSATIONALIST_INTERRUPTION_MODE", "auto"
    ).strip().lower()
    if interruption_mode == "adaptive":
        turn_handling["interruption"] = {"mode": "adaptive"}
    elif local_turn_model or interruption_mode == "vad":
        turn_handling["interruption"] = {"mode": "vad"}
    if interruption:
        turn_handling["interruption"] = {
            **turn_handling.get("interruption", {}),
            **interruption,
        }

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


def _build_llm(
    profile: CloudProfile,
    *,
    api_key: str | None = None,
    openai_plugin: object | None = None,
    sdk: object | None = None,
) -> object:
    """Build the conversational LLM, with reasoning disabled for voice.

    A reasoning-default model emits hidden reasoning tokens before any spoken
    text. Measured on qwen3.5-9b: no content at all inside a 40-token budget,
    versus 0.59 s to first token with reasoning off. Voice turns want the
    answer, not a scratchpad, so the OpenRouter path disables it by default.
    Set CONVERSATIONALIST_LLM_REASONING=auto to keep the model default.
    """
    if profile.llm_provider == "livekit":
        if sdk is None:
            from livekit import agents as sdk
        return sdk.inference.LLM(model=profile.llm_model)
    if openai_plugin is None:
        from livekit.plugins import openai as openai_plugin
    key = api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is required for selected OpenRouter stages")
    mode = os.getenv("CONVERSATIONALIST_LLM_REASONING", "off").strip().lower()
    extra: dict[str, object] = {}
    if mode not in {"auto", "on", "true", "1", "yes"}:
        extra["reasoning"] = {"enabled": False}
    return openai_plugin.LLM(
        model=profile.llm_model,
        api_key=key,
        base_url=OPENROUTER_API_BASE,
        extra_body=extra,
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
    personality = load_personality()
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
    turn_pacing = _requested_turn_pacing(metadata)
    integrations = load_integrations()
    authorized_identity = _authorized_identity(metadata)
    room_name = getattr(ctx.room, "name", None)
    agent_integrations = integrations
    if (integrations.puppeteer_stdio is not None or integrations.ensemble is not None) and (
        not authorized_identity or not isinstance(room_name, str) or not room_name
    ):
        # Never connect physical performers without a signed room participant
        # identity and a stable LiveKit room. Voice alone remains operational.
        agent_integrations = replace(integrations, puppeteer_stdio=None, ensemble=None)
    session = build_voice_session(
        profile,
        vad_sensitivity=vad_sensitivity,
        turn_pacing=turn_pacing,
        interruption=_requested_interruption(metadata),
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

    envelope_opt_in = os.getenv("CONVERSATIONALIST_OUTGOING_SPEECH_ENVELOPE") == "1"
    ensemble = None
    original_speech = tuple(
        name for name, choice in getattr(
            agent_integrations.ensemble, "motion_profiles", {},
        ).items() if choice.get("speaking") == "original_v1"
    )
    if original_speech and (len(original_speech) != 1 or not _identified_output_sdk_available()):
        # Output events do not identify the originating TTS stream. Until that
        # association is verified, startup must not silently run an idle-only
        # approximation while advertising original speech fidelity.
        logger.warning("Original speech profile unavailable: TTS/output association unverified; voice continues")
        agent_integrations = replace(agent_integrations, ensemble=None)
        original_speech = ()
    original_ready = False
    preactivation_interrupted = False
    native_bridge = None
    if agent_integrations.ensemble is not None:
        ensemble = EnsembleSession(
            agent_integrations.ensemble,
            room_id=room_name,
            caller_id=authorized_identity,
            outgoing_speech_envelope=envelope_opt_in,
            native_gain_performer=original_speech[0] if original_speech else None,
        )
        if original_speech:
            native_bridge = IdentifiedSpeechBridge(ensemble)
        ctx.add_shutdown_callback(ensemble.close)

    # The default remains the preexisting coarse semantic presence path.
    # Output events are opt-in until real LiveKit room audio and hardware
    # integration are qualified. Only the already-authorized ensemble gets
    # the output-derived presence; no PCM/transcripts leave LiveKit.
    playback_presence = (
        PlaybackPresence(
            lambda state, source, interrupted: ensemble.observe(
                state, source=source, interrupted=interrupted,
            ) if ensemble is not None and (not original_speech or original_ready) else None,
            session_id=room_name,
        )
        if ensemble is not None and (
            original_speech or os.getenv("CONVERSATIONALIST_PLAYBACK_PRESENCE") == "1"
        )
        else None
    )

    def handle_state(signal):
        nonlocal preactivation_interrupted
        if original_speech and not original_ready and signal.interrupted:
            preactivation_interrupted = True
        logger.debug(
            "conversation state changed", extra={"state": signal.state, "source": signal.source}
        )
        if embodiment is not None:
            embodiment.observe(signal.state)
        if ensemble is not None:
            if playback_presence is not None:
                playback_presence.observe(signal)
            else:
                ensemble.observe(signal.state, interrupted=signal.interrupted)

    install_session_state_hook(session, handle_state)
    room_options = room_io.RoomOptions(
        close_on_disconnect=False,
        **({"participant_identity": authorized_identity} if authorized_identity else {}),
    )
    # RoomIO resolves the explicitly linked participant while starting. The
    # room must already be connected before that resolution in Agents 1.8.
    await ctx.connect()
    if ensemble is not None:
        # A connected authorized LiveKit room must exist before this optional
        # host-owned child opens any native SDK sessions. No microphone/TTS
        # stream is moved to a robot by this step.
        try:
            await ensemble.start()
            install_ensemble_room_shutdown_hooks(
                ctx.room, ensemble, authorized_identity=authorized_identity,
            )
        except Exception:
            logger.exception("Puppeteer ensemble unavailable; continuing without robot tools")
            agent_integrations = replace(agent_integrations, ensemble=None)
            ensemble = None
    # An opt-in TTS node observes generated PCM without changing LiveKit
    # playback; Puppeteer may consume the validated features as SDK targets
    # only when its separately reviewed motion profiles and grants opt in.
    # Missing envelope support leaves the ordinary agent path.
    class _SpokenHermesNotifier:
        """Speak a finished Hermes result without being asked for status."""

        def __init__(self) -> None:
            self.session = None
            self._lock = asyncio.Lock()

        async def __call__(self, text: str) -> None:
            # Results must not be dropped just because the room was mid-turn.
            # Wait far longer for a pause, and deliver one result at a time.
            async with self._lock:
                session = self.session
                if session is None:
                    return
                deadline = time.monotonic() + 300.0
                while time.monotonic() < deadline:
                    if getattr(session, "agent_state", "idle") in {"idle", "listening"}:
                        break
                    await asyncio.sleep(0.5)
                else:
                    logger.warning("Hermes announcement forced after a long busy room")
                for attempt in range(3):
                    try:
                        logger.info("announcing finished Hermes task")
                        await session.generate_reply(
                            instructions=(
                                "A delegated Hermes task has finished. Tell the user the "
                                "result briefly and naturally in your own voice, and say "
                                "plainly if it failed or was cut off. Do not add anything "
                                "beyond this report: " + text
                            )
                        )
                        return
                    except Exception:
                        logger.exception("Hermes completion announcement failed (attempt %s)", attempt + 1)
                        await asyncio.sleep(1.0)

    hermes_notifier = _SpokenHermesNotifier()
    agent_class = (
        agent_class_with_speech_envelope(
            Agent,
            lambda frames: _offer_to_all(ensemble, frames) if envelope_opt_in else False,
            native_sink=native_bridge.offer if native_bridge is not None and ensemble is not None else None,
            tag_native_output=native_bridge is not None and ensemble is not None,
            native_begin=native_bridge.begin if native_bridge is not None and ensemble is not None else None,
            native_abort=native_bridge.abort if native_bridge is not None and ensemble is not None else None,
        )
        if (envelope_opt_in or native_bridge is not None) and ensemble is not None else Agent
    )
    initial_integrations = (
        replace(agent_integrations, ensemble=None) if original_speech else agent_integrations
    )
    initial_tools = build_assistant_tools(
        initial_integrations,
        embodiment_session=embodiment if authorized_identity else None,
        ensemble_session=ensemble if authorized_identity and not original_speech else None,
        hermes_completion_notifier=hermes_notifier,
        hermes_room=room_name,
    )
    agent = agent_class(
        instructions=_room_instructions(
            initial_integrations, caller_id=authorized_identity, session_id=room_name,
            personality=personality,
        ),
        tools=initial_tools,
    )
    await session.start(room=ctx.room, agent=agent, room_options=room_options)
    hermes_notifier.session = session
    if playback_presence is not None and ensemble is not None:
        output = getattr(getattr(session, "output", None), "audio", None)
        try:
            attached = playback_presence.attach(output)
        except Exception:
            attached = False
            logger.exception("LiveKit output playback hook unavailable; using semantic presence")
        if attached:
            async def detach_playback() -> None:
                playback_presence.detach()
            ctx.add_shutdown_callback(detach_playback)
        else:
            logger.warning("No LiveKit output playback events available; using semantic presence")
    if original_speech and ensemble is not None:
        output = getattr(getattr(session, "output", None), "audio", None)
        try:
            if (playback_presence is None or not playback_presence.attached
                    or native_bridge is None or not native_bridge.attach(output)):
                raise RuntimeError("identified LiveKit output is unavailable")
            robot_tools = build_assistant_tools(
                Integrations(ensemble=agent_integrations.ensemble), ensemble_session=ensemble,
            )
            await agent.update_tools([*initial_tools, *robot_tools])
            await agent.update_instructions(_room_instructions(
                agent_integrations, caller_id=authorized_identity, session_id=room_name,
                personality=personality,
            ))
            original_ready = True
            if not preactivation_interrupted:
                current_state = getattr(session, "agent_state", "idle")
                ensemble.observe("listening" if current_state in {"listening", "speaking"} else "idle")

            async def detach_native() -> None:
                native_bridge.detach()

            ctx.add_shutdown_callback(detach_native)
        except Exception:
            logger.exception("Identified speech output unavailable; continuing without robot")
            if native_bridge is not None:
                native_bridge.detach()
            if playback_presence is not None:
                playback_presence.detach()
            await ensemble.close()
            ensemble = None
            agent_integrations = replace(agent_integrations, ensemble=None)
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
        if ensemble is not None:
            register_robot_rpc(
                ctx.room.local_participant,
                ensemble=ensemble,
                authorized_identity=authorized_identity,
                room=ctx.room,
                capabilities={
                    "hermes": getattr(agent_integrations, "hermes", None) is not None,
                    "company_services": sorted(
                        services.connections
                    ) if (services := getattr(
                        agent_integrations, "company_services", None
                    )) else [],
                    "mcp_tools": sorted(mcp.allowed_tools)
                    if (mcp := getattr(agent_integrations, "mcp", None)) else [],
                },
            )
    # A fixed greeting needs TTS only. Asking the LLM to generate it with only
    # system instructions fails on providers that require a user query and
    # creates an unnecessary model request.
    await session.say(personality.greeting)
    # Opt-in local-only company event notifications. The trusted Python
    # worker holds the Hatchet credential; nothing reaches the browser
    # or a robot except a bounded private presentation cue and generic TTS.
    if authorized_identity:
        mail_stream_config = load_mail_stream_config()
        if mail_stream_config is not None:
            if agent_integrations.company_services is None and agent_integrations.hermes is None:
                logger.warning("mail notification demo disabled: no read-only summary capability")
            else:
                company_events = os.getenv("CONVERSATIONALIST_COMPANY_NOTIFICATIONS", "").lower() in {
                    "1", "true", "yes",
                }
                notifier = AmbientMailNotifications(mail_stream_config, allow_company=company_events)
                notifier.start(session, ctx.room)
                ctx.add_shutdown_callback(notifier.close)


def _offer_to_all(ensemble: object, frames: list[dict[str, object]]) -> bool:
    """Offer one envelope batch to *every* performer and report any success.

    ``any(...)`` would short-circuit on the first performer, so only the first
    configured robot ever received speech targets. Each performer is gated
    independently, and one being dropped for backpressure must not silence
    the others.
    """
    offer = getattr(ensemble, "offer_speech_envelope", None)
    if offer is None:
        return False
    accepted = False
    for performer in ensemble.config.performer_ids:  # type: ignore[attr-defined]
        if offer(frames, performer_id=performer):
            accepted = True
    return accepted


def _room_instructions(
    integrations: object,
    *,
    caller_id: str | None,
    session_id: str | None,
    personality: Personality = DEFAULT,
) -> str:
    """Add only the operator-pinned Reachy move IDs to this room's guidance."""
    base = INSTRUCTIONS
    if personality.instructions:
        base = base.replace("You are Forge Conversationalist, a helpful spoken assistant. ", "")
        base = personality.instructions + " " + base
    if any(
        os.getenv(name, "").lower() in {"1", "true", "yes"}
        for name in ("CONVERSATIONALIST_MAIL_STREAMS", "CONVERSATIONALIST_COMPANY_NOTIFICATIONS")
    ):
        base += (
            " With operator-enabled company event notifications, I may announce "
            "generic new email, order and workflow/print-failure alerts and ask "
            "whether the user wants details. A yes is authorization to request "
            "a read-only summary only through currently granted company services "
            "or Hermes tools; never guess, claim work succeeded, read mail "
            "contents without an explicit yes, or send/modify email. "
            "If no current read grant exists, say details are unavailable."
        )
    config = getattr(integrations, "puppeteer_stdio", None)
    if getattr(integrations, "ensemble", None) is not None:
        return base + (
            " One operator-authorized Puppeteer room may mirror approximate "
            "listening/speaking/idle states across its configured performers. These states "
            "are not speech sample timestamps, nor are robot movements evidence "
            "of completed company tasks. Use get_ensemble_status to inspect "
            "individual outcomes and each robot's exact approved expressions. "
            "Use request_ensemble_gesture only when the user explicitly requests "
            "a gesture for a selected performer; first discover the name and "
            "performer_id from get_ensemble_status. Never issue raw motion, "
            "infer physical completion from "
            "accepted admission, or retry unknown outcomes. If a gesture was accepted, "
            "say it was requested, not that it happened. If a performer is blocked, "
            "say its outcome is unconfirmed; never promise it will settle or resume. "
            "Early in the conversation call get_ensemble_status once. If it lists "
            "startup_questions, a robot is holding still because it is not at its sleep "
            "position: say which robot and how far off it is, and ask whether to move it to "
            "sleep. Only after the user says yes, call prepare_robot_for_conversation with "
            "action move_to_sleep (or confirm_at_sleep if the user says a robot that cannot "
            "measure itself is already parked). Never move a robot without that yes, and "
            "never retry a failed move."
        )
    if config is None:
        if getattr(integrations, "embodiment", None) is not None:
            return base + (
                " A fake-only Puppeteer embodiment is configured. Use "
                "discover_performer to list its exact simulated expression names "
                "and revisions, and request_performer_expression only when a "
                "person explicitly requests expression. This is simulation, not "
                "proof of physical robot motion. Check accepted action IDs with "
                "check_performer_action; do not retry unknown outcomes. "
                "Speaking/listening state is approximate, not a confirmed audio "
                "playback clock."
            )
        return base
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
    return base + guidance


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
    """Read a supported VAD preset, from room metadata or the worker default.

    The browserless audio participant sends no metadata, so without an
    environment default every room ran the SDK's most eager threshold and
    transcribed background noise as words.
    """
    fallback = os.getenv(
        "CONVERSATIONALIST_VAD_SENSITIVITY", "standard"
    ).strip().lower()
    if fallback not in VAD_SENSITIVITY_THRESHOLDS:
        fallback = "standard"
    if not metadata:
        return fallback
    try:
        request = json.loads(metadata)
    except (TypeError, json.JSONDecodeError):
        return fallback
    sensitivity = request.get("vad_sensitivity") if isinstance(request, dict) else None
    return (
        sensitivity
        if isinstance(sensitivity, str) and sensitivity in VAD_SENSITIVITY_THRESHOLDS
        else fallback
    )


_BARGE_IN_PRESETS = {
    "off": {"enabled": False},
    "hard": {"enabled": True, "min_words": 3, "min_duration": 0.6},
    "standard": {"enabled": True, "min_words": 1, "min_duration": 0.3},
    "easy": {"enabled": True, "min_words": 0, "min_duration": 0.1},
}


def _requested_interruption(metadata: str) -> dict[str, object]:
    """Read operator-chosen barge-in behaviour from signed job metadata.

    Only bounded, allowlisted fields are accepted. A malformed request falls
    back to the configured default rather than guessing. The default keeps
    barge-in but never restarts an interrupted sentence: resuming is what made
    the greeting stutter and repeat when the microphone picked up the speakers.
    """
    options = _default_interruption()
    if not metadata:
        return options
    try:
        request = json.loads(metadata)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(request, dict):
        return {}
    raw = request.get("interruption")
    if not isinstance(raw, dict):
        return options
    enabled = raw.get("enabled")
    if isinstance(enabled, bool):
        options["enabled"] = enabled
    mode = raw.get("mode")
    if mode in {"adaptive", "vad"}:
        options["mode"] = mode
    min_words = raw.get("min_words")
    if isinstance(min_words, int) and not isinstance(min_words, bool) and 0 <= min_words <= 10:
        options["min_words"] = min_words
    min_duration = raw.get("min_duration")
    if isinstance(min_duration, (int, float)) and not isinstance(min_duration, bool) and 0.0 <= float(min_duration) <= 2.0:
        options["min_duration"] = float(min_duration)
    resume = raw.get("resume_false_interruption")
    if isinstance(resume, bool):
        options["resume_false_interruption"] = resume
    return options


def _default_interruption() -> dict[str, object]:
    preset = os.getenv("CONVERSATIONALIST_BARGE_IN", "hard").strip().lower()
    options: dict[str, object] = dict(_BARGE_IN_PRESETS.get(preset, _BARGE_IN_PRESETS["hard"]))
    # Never resume a sentence that was cut off by a false interruption.
    options.setdefault("resume_false_interruption", False)
    return options


def _requested_turn_pacing(metadata: str) -> str:
    try:
        request = json.loads(metadata)
    except (TypeError, json.JSONDecodeError):
        return "standard"
    value = request.get("turn_pacing") if isinstance(request, dict) else None
    return value if isinstance(value, str) and value in {"quick", "standard", "patient"} else "standard"


def _prewarm_job(_process: object) -> None:
    """Import latency-sensitive modules before the first turn.

    Tool discovery and the realtime LLM pull in large schema packages. Doing
    that inside the first reply stalls the event loop for a few hundred
    milliseconds, which is audible as a late answer.
    """
    import importlib

    for name in (
        "mcp",
        "mcp_types",
        "openai",
        "livekit.agents.llm.mcp",
        "livekit.plugins.openai",
    ):
        try:
            importlib.import_module(name)
        except Exception:
            logger.debug("prewarm import skipped: %s", name, exc_info=True)


def main() -> None:
    from dotenv import load_dotenv
    from livekit import agents
    from livekit.agents import AgentServer

    load_dotenv(".env.local")
    profile = load_profile()
    load_integrations()
    if profile.requires_openrouter and not os.getenv("OPENROUTER_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY is required for selected OpenRouter stages")
    server = AgentServer(setup_fnc=_prewarm_job)

    server.rtc_session(agent_name=AGENT_NAME)(entrypoint)

    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
