# Mostly linear PR program

PR 0 (main): minimal README only, to initialize the empty GitHub repository.
PR 1 (this branch): standalone LiveKit room, browser UI, voice worker,
    LiveKit Inference profile, Silero VAD, native turn detection,
    text fallback, gated room tokens, offline tests, cloud acceptance plan.
PR 2: OpenRouter STT/LLM/TTS adapters and hybrid profile, only after PR 1 is merged.
PR 3: standard authenticated MCP client and optional ContextForge virtual server.
PR 4: Hermes/external agent session adapter.
PR 5: multiple participants, microphones, speakers and agent routing policy.
PR 6: optional Puppeteer integration after its required contracts merge.

Separate PRs where new capabilities are independent. Keep live provider
and hardware qualification attended. Never treat draft sibling PRs as main.
