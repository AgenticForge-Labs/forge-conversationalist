# Forge Conversationalist

A hardware-independent voice conversation app for AgenticForge Labs.

**PR 1 state:** a standalone LiveKit Agents voice worker and a minimal
Next.js web client. No robot, MCP server, Hermes instance, local LLM, or
OpenRouter account is needed. Silero VAD runs locally on CPU; STT, LLM,
TTS and the speech turn detector use LiveKit Cloud Inference.

**This branch is not production-deployed.** Live microphone/cloud audio,
pricing, and browser compatibility still need an authenticated local
acceptance test before merge.

## Quick start (LiveKit Cloud)

1. Create a LiveKit Cloud project and obtain its `LIVEKIT_URL`,
   `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.
2. Copy `.env.example` to `.env.local` at the repository root. Set
   LiveKit credentials and a long random `CONVERSATIONALIST_ACCESS_TOKEN`.
   **Never commit `.env.local`.**
3. Install the Python worker in an isolated environment:

   ```bash
   uv venv --python 3.12
   uv pip install --python .venv/bin/python -e '.[dev]'
   .venv/bin/python -m forge_conversationalist.agent download-files
   .venv/bin/python -m forge_conversationalist.agent dev
   ```

4. Configure the browser token endpoint separately:

   ```bash
   cp web/.env.example web/.env.local
   # Copy the same LiveKit credentials and access token into web/.env.local.
   cd web
   npm install
   npm run dev
   ```

5. Visit [http://localhost:3000](http://localhost:3000), enter the
   `CONVERSATIONALIST_ACCESS_TOKEN` configured in the local web server,
   and click **Start conversation**. Allow microphone access. LiveKit
   explicitly dispatches one `forge-conversationalist` agent to a unique
   room. Text input uses LiveKit's `lk.chat` text stream.

The worker uses the documented LiveKit `AgentServer`, `AgentSession`,
`inference.STT/LLM/TTS`, Silero VAD, and hosted `inference.TurnDetector`.
All model IDs and voice selection are configurable via the environment.

## Tests

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
cd web && npm install && npm run check && npm run build
```

These tests require no cloud credentials, robots or microphones. They
**do not** prove that a live cloud conversation works.

## Security and data

The token endpoint is a **single-operator development gate**, NOT production
authentication. It requires a configured long access secret, generates
short-lived scoped room tokens, and never returns the LiveKit API secret.
Do not expose the development web server to the internet or deploy it
as a multi-tenant service. Upgrade authentication, rate limiting and
security audit before any public deployment.

The app itself does not persist transcripts or audio. LiveKit/provider
processing, tracing and retention depend on external service settings.
No automatic recording is enabled in this repository.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/PR_PLAN.md](docs/PR_PLAN.md), and [AGENTS.md](AGENTS.md).
OpenRouter, Hermes, MCP, multi-speaker routing and Puppeteer belong to
later PRs, not hidden or partially enabled features of this branch.
