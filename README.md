# Forge Conversationalist

A hardware-independent voice conversation app for AgenticForge Labs,
built on LiveKit Agents. Conversation needs no robot or local LLM.
PR #1 (unmerged) adds realtime voice and selectable LiveKit/OpenRouter
inference. This stacked PR adds **optional** MCP tools and Hermes
delegation to the same lightweight LiveKit Agent. Both PRs remain
unmerged, and real provider/cloud acceptance is outstanding.

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
