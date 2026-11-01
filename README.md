# Forge Conversationalist

A hardware-independent LiveKit voice conversation app for AgenticForge Labs.
**No robot, local LLM, Hermes, MCP gateway or Platform server is required.**

**Open draft PR 1, not production:** includes a LiveKit room/voice worker and
browser UI plus selectable OpenRouter, LiveKit Inference, and hybrid profiles.
Real cloud audio compatibility and interruption latency are **not yet
attended-tested**. Credentials are needed only on the trusted worker.

## Quick start

1. Create a LiveKit Cloud project. Copy `.env.example` to `.env.local`
   and set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`,
   and a long random `CONVERSATIONALIST_ACCESS_TOKEN`.
2. Select a profile in the Python worker's `.env.local`:

   | Profile | STT | LLM | TTS |
   | --- | --- | --- | --- |
   | `livekit` | LiveKit Inference | LiveKit Inference | LiveKit Inference |
   | `openrouter` | OpenRouter | OpenRouter | OpenRouter |
   | `hybrid` | LiveKit Inference | OpenRouter | LiveKit Inference |

   `CONVERSATIONALIST_PRESET=openrouter` requires
   `OPENROUTER_API_KEY` in the **Python worker only**. By default, it
   selects OpenRouter Parakeet TDT (buffered transcription),
   `qwen/qwen3.5-9b` and Qwen TTS Flash with voice `loongjohn`.
   This is **not** Reachy's Aiden voice. See `.env.example` for
   independent `CONVERSATIONALIST_*_PROVIDER`, model and voice fields.

3. Install and start the standalone worker:

   ```bash
   uv venv --python 3.12
   uv pip install --python .venv/bin/python -e '.[dev]'
   .venv/bin/python -m forge_conversationalist.agent download-files
   .venv/bin/python -m forge_conversationalist.agent dev
   ```

4. Copy `web/.env.example` to `web/.env.local`. Supply only LiveKit
   connection credentials and the **development access token**; **do not
   put OpenRouter keys in the web env file**.

   ```bash
   cd web
   npm install
   npm run check && npm run build
   npm run dev
   ```

5. Visit http://localhost:3000, enter the development access token and
   start a conversation. Permit microphone use. LiveKit dispatches one
   agent to a random room. Typed chat uses its `lk.chat` text stream.

Switch profiles between sessions by changing the trusted worker
configuration and restarting the worker. No unsafe mid-utterance provider
hot-switching or hidden billable fallback is implemented.

## Testing

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
cd web && npm install && npm run check && npm run build
```

CI uses mocks and never calls OpenRouter or starts a paid voice session.
Actual deployment acceptance must verify OpenRouter transcription format,
LLM streaming, TTS voice and MP3 decoding, audible interruptions, p50/p95
latency, cost, and browser connection lifecycle with real credentials.
OpenRouter's non-realtime STT won't yield partial words continuously.

## Security and data

The `POST /api/session` endpoint has a **development-only**
single-operator passphrase gate, not production authentication.
It generates short-lived scoped room tokens; LiveKit API secrets and
OpenRouter API keys must not be exposed to browsers. Never deploy the
development web service publicly or as a multi-tenant system.

This repo doesn't retain audio or transcripts. Remote AI service data
handling is governed by the selected provider settings. No recording is
enabled here.

## Ownership

LiveKit owns realtime transport and turn management. This app selects
models and exposes the conversation client; Hermes owns any future
agent state, Platform owns business state/evidence, Puppeteer owns
expression, and device SDKs own physical safety.

See [AGENTS.md](AGENTS.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
and [docs/PR_PLAN.md](docs/PR_PLAN.md). No Hermes, MCP, robots or
multi-participant arbitration are implemented yet.
