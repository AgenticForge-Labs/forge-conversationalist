# Repository development rules

Read organization-level `development.md` and `architecture.md` first.
GitHub main is the authoritative integrated state; inspect open PRs.
PRs #1–#3 are merged into main. Puppeteer native three-robot ensemble PR #13 is merged; this repository's PR #9 connects an authorized LiveKit room to its fixed semantic MCP interface.

## Boundaries
- LiveKit owns realtime audio, rooms, VAD and playback.
- Conversationalist owns its lightweight agent configuration and optional tools.
- Use native LiveKit MCPToolset for MCP, with exact server-side authorized grants;
  do not create a second gateway or trust client-side filtering as security.
- Hermes owns its API session, toolset, runs, memory and longer work. Delegate
  using its authenticated Runs API; do not clone its agent/session authority.
- Platform owns operational evidence and ContextForge tool federation.
- Puppeteer owns semantic robot performance; SDKs own hardware execution and safety.

## Safety and tests
- All MCP/Hermes credentials stay in Python worker env, not web/.env.local.
- MCP must have a nonempty explicit tool allowlist and scoped credential; no
  server-provided wildcard tools by default.
- Consequential writes and robot motion require upstream owner approvals and
  hardware safety. This repo exposes no raw robot driver.
- Hermes submissions return admission only; poll actual status to claim a
  completed task. Do not automatically retry ambiguous submissions.
- No Hermes TUI/CLI session takeover via API. No automatic completion push.
- Cloud/provider authentication, microphone playout, MCP, Hermes and physical
  operations need attended acceptance; unit-test green is not production ready.
- Keep PR statuses and exact SHAs in handoff.

## Reusable fake-only embodiment v1
- Puppeteer PR #10 merged `forge.embodiment.v1` to main. The optional
  `PUPPETEER_EMBODIMENT_CONFIG` path is a strictly fake-only MCP child;
  avoid duplicating robot SDKs and never interpret simulated expressions as
  physical qualification.
- Use `SessionStateHook` for approximate `agent_state` presence, with
  versioned sequence and periodic state refresh. Do not call these measured
  output start/drain times. Never automatically retry unknown action outcomes.
- One room-owned MCP child handles both observation and explicit expression.
  Do not start a parallel process for the same room's expressions.
- Preserve no-robot startup and the separate restricted Reachy MCP path.

## Device-independent presence and notifications
- Audio belongs to a LiveKit participant, not to a particular robot or
  personality. Browser audio selectors can change only supported devices.
- Canonical presentation schema is `forge.presentation.v1` in
  `presentation.py`: idle, listening, speaking; neutral, happy, curious,
  confused, concerned, excited; mail, order, failure generic indicators.
- Platform (or owning producer) retains mail/order/error truth and replay
  authority; no event subscription is implemented by this repo.
- Renderers may use icons, light, sound or approved Puppeteer semantics.
  No microphone-loudness-driven motion, raw motor commands or silent
  widening of robot permissions. No subscriber should trust an unverified
  LiveKit room packet as evidence of an actual company event.
- Never automatically speak private notification content; browser
  notification examples must be clearly labeled manual previews.


## Native robot expression controls (2026-11-02)
- Only Puppeteer owns the authoritative, approved finite expression catalog;
  fetch `ensemble_state.performers[*].expressions` from the same room-owned
  MCP child, never guess which gestures a robot has.
- UI and agent tools must use explicit `performer_id` and a currently
  catalogued name. Automatic speaking/listening/idle are NOT manual gestures.
- Both robot RPC methods are registered only after authorized ensemble
  startup and are guarded by the signed LiveKit participant identity.
  Never move motion policy or hardware permission to the browser.
- Admission is not physical completion; preserve no retries and honest
  unknown outcomes. Availability and quarantine remain Puppeteer/SDK-owned.
- Do not silently import every upstream recording: an immutable reviewed
  behavior pack is the sole selection boundary.
