"""Standalone, no-robot LiveKit voice worker.

No Hermes, MCP, Puppeteer or hardware integration is imported here.
The worker uses LiveKit Cloud Inference; its tiny local Silero VAD is not
an LLM or a remotely addressable model service.
"""

from __future__ import annotations

from .config import AGENT_NAME, CloudProfile, load_profile

INSTRUCTIONS = (
    "You are Forge Conversationalist, a helpful spoken assistant. "
    "Answer clearly, accurately, and briefly by default. "
    "Use normal conversational speech rather than Markdown or long lists. "
    "Never claim to have external tools, memory, robot control, or private "
    "business information when those integrations are unavailable."
)


def build_voice_session(profile: CloudProfile, *, sdk: object | None = None) -> object:
    """Construct a LiveKit voice session; SDK can be substituted by offline tests."""
    if sdk is None:
        from livekit import agents as sdk

    from livekit.plugins import silero

    return sdk.AgentSession(
        stt=sdk.inference.STT(model=profile.stt_model, language=profile.stt_language),
        llm=sdk.inference.LLM(model=profile.llm_model),
        tts=sdk.inference.TTS(model=profile.tts_model, voice=profile.tts_voice),
        vad=silero.VAD.load(),
        turn_handling=sdk.TurnHandlingOptions(turn_detection=sdk.inference.TurnDetector()),
    )


def main() -> None:
    from dotenv import load_dotenv
    from livekit import agents
    from livekit.agents import Agent, AgentServer

    load_dotenv(".env.local")
    profile = load_profile()
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
