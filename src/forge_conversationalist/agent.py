"""Standalone, cloud-inference LiveKit voice worker.

LiveKit remains the only realtime media/room/turn engine. Existing LiveKit
provider plugins own OpenRouter API request assembly; no duplicate audio
transport, model client, agent memory, MCP gateway or robot is created here.
"""

from __future__ import annotations

import os

from .config import AGENT_NAME, OPENROUTER_API_BASE, CloudProfile, load_profile

INSTRUCTIONS = (
    "You are Forge Conversationalist, a helpful spoken assistant. "
    "Answer clearly, accurately, and briefly by default. "
    "Use normal conversational speech rather than Markdown or long lists. "
    "Never claim to have external tools, memory, robot control, or private "
    "business information when those integrations are unavailable."
)


def build_voice_session(
    profile: CloudProfile,
    *,
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

    from livekit.plugins import silero

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
    return sdk.AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        vad=silero.VAD.load(),
        turn_handling=sdk.TurnHandlingOptions(turn_detection=sdk.inference.TurnDetector()),
    )


def main() -> None:
    from dotenv import load_dotenv
    from livekit import agents
    from livekit.agents import Agent, AgentServer

    load_dotenv(".env.local")
    profile = load_profile()
    if profile.requires_openrouter and not os.getenv("OPENROUTER_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY is required for selected OpenRouter stages")
    server = AgentServer()

    class Conversationalist(Agent):
        def __init__(self) -> None:
            super().__init__(instructions=INSTRUCTIONS)

    @server.rtc_session(agent_name=AGENT_NAME)
    async def entrypoint(ctx: agents.JobContext) -> None:
        session = build_voice_session(profile)
        await session.start(room=ctx.room, agent=Conversationalist())
        await session.generate_reply(
            instructions="Greet the person briefly and invite them to speak."
        )

    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
