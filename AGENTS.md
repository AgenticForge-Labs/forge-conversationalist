# Repository development rules

Read the organization-level `development.md` and `architecture.md` first.
GitHub `main` is authoritative; inspect open PRs before branching.

## Ownership
- Forge Conversationalist owns conversation UI, provider choice and adapters.
- LiveKit owns realtime rooms, VAD, turn detection, streaming and playout.
- Hermes owns existing agent sessions, memory and tools.
- Platform / ContextForge owns MCP grants, operational evidence and shared state.
- Puppeteer owns performer coordination; device SDKs own motion and safety.
- Never implement a competing audio engine, gateway, agent memory store or robot driver here.

## Security, providers and tests
- OpenRouter and LiveKit inference may be independently selected for STT,
  LLM and TTS; configure via trusted environment, never browser requests.
- Never put OpenRouter keys in web/.env.local or NEXT_PUBLIC_* variables.
- The fixed OpenRouter base URL is owned by this backend and not user input.
- OpenRouter transcription uses HTTP (non-realtime) until genuine provider
  support for streaming STT is verified. No implied low-latency guarantee.
- Validate model voice IDs against each provider's current catalog in
  attended acceptance; don't assume a Qwen voice is Reachy's Aiden.
- Offline CI mocks providers; cloud audio and real costs require attended testing.
- No local LLM, robot, Hermes session or MCP server is required for startup.
- Do not merge with live audio acceptance incomplete unless explicitly deferred.
