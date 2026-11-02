# Reusable Puppeteer embodiment reference integration

**Scope:** Optional Conversationalist voice integration with merged Puppeteer
`forge.embodiment.v1`, introduced by
[forge-puppeteer PR #10](https://github.com/AgenticForge-Labs/forge-puppeteer/pull/10)
(merge `8607748f727c0b694398d3ad29de0c89d499833b`).
The current reference client accepts **only fake backends** and cannot
connect to a robot SDK, hardware port, broker or physically authorized profile.

## Ownership, reusable boundary and signal sources

- LiveKit owns realtime media, VAD, playback and the session event callbacks.
- Conversationalist owns conversation and emits content-free semantic states.
- Puppeteer owns discovery, one-shot expression request mapping, action identity,
  resources, coordinator policy and the MCP tools.
- Native device SDKs, SO-ARM broker and robot controllers retain all physical
  authorization, validation, calibration, STOP and fault semantics.

Conversationalist remains usable **without Puppeteer**. Puppeteer runs on its
own as an MCP stdio server for text-based MCP agents, deterministic scripts,
or Studio productions. No event bus or robot-specific voice state machine is
introduced. `forge.presentation.v1` remains the existing device-independent
presentation cue vocabulary for *other* consumers; it is **not** a secure
general-purpose business event bus.

When the voice worker is enabled, `SessionStateHook` receives trusted
LiveKit `agent_state_changed` and `user_state_changed` notifications and
emits `idle`, `listening`, `speaking` on the same MCP child as agent tools.
They are marked `source=agent_state`, meaning **approximate**, not
confirmed audio device start/drain or per-phoneme timing. The existing
post-playout `AssistantTextCompleted` callback is independent and does
not establish a precise stream-start timestamp. No transcript or PCM is sent
to Puppeteer. Voice-state observations have increasing sequence numbers and
fresh IDs; a 10-second heartbeat refreshes the last state for expiry.
MCP failures stop acceptance; ambiguous expression submissions are not
automatically retried. Shutdown sends a `disconnected` state observation
as a best-effort event. MCP client process lifetime is the room lifetime.

If the backend provides a verified audible playback lifecycle in the future,
a separately reviewed adapter may send `source=playback`. Stock Reachy
Conversation App revision `5ab905d018de8d88f07444ca57677b2ffbe762ca`
and Reachy SDK `1.10.0rc5` do not yet provide the required end-to-end
handoff through the existing `forge-reachy` external extension. Its
open stacked draft PRs #3/#4 remain unmerged and must not be conflated
with this reference integration.

## Installation: three independent fake configurations

The Puppeteer package is not assumed published on PyPI. Install its exact
merged GitHub revision into the **voice worker environment** (no sibling
source checkout needed):

```bash
python -m pip install 'forge-puppeteer[mcp] @ git+https://github.com/AgenticForge-Labs/forge-puppeteer.git@8607748f727c0b694398d3ad29de0c89d499833b'
python -m pip install -e '.[dev]'
```

For each performer choose exactly one configuration from the *installed or
copied operator configuration* corresponding to the Puppeteer v1 examples:

- `examples/robots/embodiment-reachy.fake.json` →
  `reachy-sim`, `device=reachy`.
- `examples/robots/embodiment-stackchan.fake.json` →
  `stackchan-sim`, `device=stackchan`.
- `examples/robots/embodiment-soarm101.fake.json` →
  `soarm101-sim`, `device=soarm101`.

Copy the selected JSON from the pinned Puppeteer source to an absolute,
operator-owned path such as `/opt/agenticforge/puppeteer/performer.json`,
or write the exact JSON config below, selecting only one device at a time:

```json
{
  "backend": "fake",
  "device": "reachy",
  "instance_id": "reachy-sim",
  "name": "Reachy software embodiment",
  "allowed_tools": [
    "device_info", "action_lookup", "performer_discover",
    "conversation_presence", "expression_request"
  ],
  "assets": [
    "emotion:curious", "emotion:happy", "emotion:confused",
    "emotion:neutral", "dance:hello", "gesture:acknowledge"
  ]
}
```

In Conversationalist's **root** `.env.local` only:

```bash
PUPPETEER_EMBODIMENT_CONFIG=/opt/agenticforge/puppeteer/performer.json
# Optional if the installed binary is on PATH:
PUPPETEER_EMBODIMENT_COMMAND=/absolute/path/to/forge-puppeteer-mcp
```

Start Conversationalist normally:

```bash
python -m forge_conversationalist.agent dev
# In another shell: cd web && npm install && npm run dev
```

Set no `PUPPETEER_EMBODIMENT_*` variables to start **without a robot**.
The strict config requires the fake backend, exact tool grants, one
`instance_id`, and no physical authority. Unexpected tools including STOP
cause startup rejection. Other existing integrations, such as restricted
Reachy physical moves, remain independent and off by default.

If the LiveKit job has a trusted `authorized_identity`, the agent also
receives three **bounded** tools that all use its same room's MCP child:
`discover_performer`, `request_performer_expression` and
`check_performer_action`. First discover exact names and revisions,
then request one named simulated expression; use the returned action ID
for lifecycle lookup. An unauthorized participant cannot activate these
agent tools through room metadata alone. One-shot requests may be denied
when the observed state is busy. A returned `accepted` action does not
mean the SDK moved, speech played or a physical STOP occurred.

## MCP and direct-client examples

Run Puppeteer standalone by starting `forge-puppeteer-mcp --config
/path/performer.json` as an MCP stdio child. Tool inputs are:

```json
{
  "event": {
    "schema": "forge.embodiment.v1",
    "event_id": "session-a-listening-001",
    "session_id": "room-a",
    "performer_id": "reachy-sim",
    "sequence": 0,
    "state": "listening",
    "source": "manual",
    "interrupted": false
  }
}
```

```json
{
  "request": {
    "schema": "forge.embodiment.v1",
    "action_id": "expression-001",
    "caller_id": "text-agent",
    "session_id": "room-a",
    "performer_id": "reachy-sim",
    "kind": "emotion",
    "name": "curious",
    "asset_revision": "sha256:forge-puppeteer-fake-v1"
  }
}
```

For this fake setup, send an `idle` presence event before the expression
if a prior listening/speaking state would conflict. This fake revision
is **not** a physical asset hash. In physical modes, only a real SDK-owned,
operator-pinned asset revision is acceptable. A text agent may explicitly
request a curious pose, but unknown expressions never randomly substitute
another pose. Deterministic callers use the same MCP tools and must check
`action_lookup` for terminal status.

## Test and qualification boundary

```bash
pytest -q tests/test_embodiment.py
pytest -q
ruff check src tests
cd web && npm run check && npm run build
```

The tests use independent fake MCP stdio child servers and verify room startup,
three selected device types, authorized exact-expression tools, ordering,
status lookup, optional absence of the bridge and shutdown. Puppeteer's
PR #10 separately tests all three *actual Puppeteer* fake MCP backends.

**Deferred/physical qualification:** no head/antenna blending, recorded
emotion/dance mapping to physical devices, SO-ARM motor idle,
speaker-output timestamps, confirmed cancellation, queue-drain feedback,
hardware stop, shared manipulation resources or unattended physical
acceptance. No firmware, calibration, native SDK or broker changes are
required to run these tests.
