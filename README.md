# Forge Conversationalist

A hardware-independent voice conversation app for AgenticForge Labs,
built on LiveKit Agents. **Conversation needs no robot or local LLM.**

PRs #1 and #2 are merged into main: LiveKit/OpenRouter/hybrid speech
profiles plus optional authenticated MCP and Hermes delegation.
Real provider/cloud/hardware acceptance is still outstanding.

PR #3 adds device-independent presentation signals and optional browser
microphone/speaker selection. It does **not** add a Platform subscription,
robot motion, or a new realtime audio engine.

## Run the voice application

1. Create a LiveKit Cloud project. Copy `.env.example` to root `.env.local`.
   Supply `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` and a long
   random `CONVERSATIONALIST_ACCESS_TOKEN`.
2. Set `CONVERSATIONALIST_PRESET=livekit|openrouter|hybrid`. Add
   `OPENROUTER_API_KEY` to **root only** when an OpenRouter stage is selected.
3. Install the Python worker and model assets:

   ```bash
   uv venv --python 3.12
   uv pip install --python .venv/bin/python -e '.[dev]'
   .venv/bin/python -m forge_conversationalist.agent download-files
   .venv/bin/python -m forge_conversationalist.agent dev
   ```

4. Copy `web/.env.example` to `web/.env.local`. Supply LiveKit credentials
   and the **development access token** only, not OpenRouter/MCP/Hermes keys.
   Start `cd web && npm install && npm run dev`; visit http://localhost:3000.

For a loopback-only LiveKit development server, set both env files to
`LIVEKIT_URL=ws://localhost:7880`, `LIVEKIT_API_KEY=devkey`, and
`LIVEKIT_API_SECRET=secret`; start `livekit-server --dev` bound to loopback.
Set `CONVERSATIONALIST_TURN_DETECTOR_VERSION=v1-mini` in the worker env to run
the turn detector locally. This loads a roughly 244 MB model and uses VAD-based
interruption handling; the default cloud setup keeps LiveKit's automatic turn
detector and adaptive interruption selection.
The pre-session **Microphone sensitivity** setting adjusts Silero's speech
activation threshold: **Less sensitive** filters quieter sounds, **Standard**
uses the existing default, and **More sensitive** detects quieter speech. It
applies to the next conversation.

Before starting a conversation, the browser offers the configured TTS profile
and OpenRouter speech models listed at $20 or less per million input characters
or bytes. Prices and voice IDs come from OpenRouter's public speech model
catalog, refreshed every five minutes. Models with no published voice list are
shown but cannot be selected. Models with a documented output format use it;
other models request MP3 and are marked as unverified in this LiveKit adapter.
Token-priced and duration-priced models are excluded because they cannot be
honestly compared using a per-character price. A model/voice selection is
validated by the session API and again in the worker before being used. The
OpenRouter key remains in the worker environment; the browser only receives
catalog metadata and the existing LiveKit session token.

## Optional lightweight agent integrations

**MCP** is off by default. Enable with a *trusted*, authorized MCP URL,
scoped bearer credential and explicit tool-name allowlist in root
`.env.local`:

```bash
MCP_URL=http://127.0.0.1:4444/mcp
MCP_TOKEN=YOUR_SCOPED_MCP_JWT
MCP_ALLOWED_TOOLS=data_read,work_status
```

These are **illustrative names from Platform's fake development MCP child**,
not a promise the unified ContextForge gateway is running. Use exact tool
names from your actually configured gateway. LiveKit MCPToolset is responsible
for MCP client transport/discovery; the MCP gateway remains responsible for
all authorization, approvals and hardware safety. The local MCP URL is an
example, not an activated production service. Prefer read-only grants.

**Hermes delegation** is also off by default:

```bash
HERMES_DELEGATION_ENABLED=true
HERMES_API_URL=http://127.0.0.1:8642
HERMES_API_KEY=YOUR_HERMES_API_SERVER_KEY
```

Configure `API_SERVER_ENABLED=true`, `API_SERVER_KEY` in Hermes' own
settings and run `hermes gateway`. The API typically binds loopback port
8642; use a separate constrained Hermes profile because Hermes can have
powerful tools. No browser-to-Hermes connection is required.

Say \"Ask Hermes to research X\". Conversationalist invokes
`delegate_to_hermes` once, receives a run ID, and truthfully reports it
as *started*. Later say \"Check that Hermes task\" to invoke
`check_hermes_run`; it reports the actual status or final output.
There is no automatic task-completion announcement yet, no approval-by-
voice, and this does not attach to your existing TUI/CLI Hermes session.

With no integrations configured, Conversationalist has no extra tools
and simply responds as a cloud voice assistant.

## Audio devices, expressions and notifications

The web UI lets each connected participant select a supported microphone
and speaker. LiveKit audio tracks—not Reachy or Puppeteer—own the
conversation media transport. Browser restrictions can prevent selecting
some speaker outputs. A future Reachy, room-audio, phone or other endpoint
can join the same room with its own supported audio adapter.

The page also has **manual preview-only** emotion and notification controls,
even when no robot or LiveKit room is present. New mail is a soft ding,
new order a double ding, and an error a warning chime; no Platform event
feed or user mail/orders are read or simulated as actual records.
Cross-system event subscriptions, publisher/subscriber authorization, replay,
notification fanout and delivery policy belong to **AgenticForge Platform**.
Conversationalist only translates authorized events into cues. LiveKit room
data messages are not a general-purpose bus.
Preview chimes are optional/mutable. The semantic listening/speaking/idle
states, six small emotions and three notification kinds share one Python
`forge.presentation.v1` contract, documented in
[docs/PRESENTATION_SIGNALS.md](docs/PRESENTATION_SIGNALS.md).
All event content defaults to generic labels with **no automatic speech**.
Puppeteer may later render states/emotions via authorized native device
primitives; no audio amplitude wobble or robot driver is introduced here.

## Tests and safety

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
cd web && npm install && npm run check && npm run build
```

The app does not persist transcripts or audio. AI provider and LiveKit
service retention depends on external settings. The development
`POST /api/session` token gate is NOT production authentication; do not
expose it publicly. No physical motion, purchasing or other consequential
capability should be enabled without upstream validation and approval.
Tests mock all external calls; real LiveKit, OpenRouter, Hermes and MCP
integration require separate attended local acceptance.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/PR_PLAN.md](docs/PR_PLAN.md), and [AGENTS.md](AGENTS.md).
