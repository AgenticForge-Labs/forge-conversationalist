# Repository development rules

Read the organization-level `development.md` and `architecture.md` first.
GitHub `main` is the authoritative integrated state; review open PRs before branching.

## Ownership
- Forge Conversationalist owns voice UI, LiveKit session configuration, and optional adapters.
- LiveKit owns WebRTC, VAD/turn detection, session streaming, interruptions, and playout.
- Hermes owns the lifecycle, memory, and tools of its existing agents.
- AgenticForge Platform / ContextForge owns its virtual MCP tool catalog, grants, and operational evidence.
- Forge Puppeteer owns performer coordination; SDKs own motion and safety.
- Never implement a competing gateway, LLM memory store, hardware driver, or voice transport here.

## Security and tests
Do not commit secrets or turn on unauthenticated room-token issuance.
Mock cloud inference and server SDKs in CI; real provider and device acceptance is explicitly attended.
No local robot or LLM is required.
Do not merge on green unit tests alone if the PR requires live cloud audio acceptance.
Keep model/provider identifiers configurable and record observed provider latency and costs in testing.
