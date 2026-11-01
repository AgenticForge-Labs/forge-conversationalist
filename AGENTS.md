# Repository development rules

Read organization-level `development.md` and `architecture.md` first.
GitHub main is the authoritative integrated state; inspect open PRs.
PR #2 is stacked on unmerged PR #1; preserve dependency and merge in order.

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
