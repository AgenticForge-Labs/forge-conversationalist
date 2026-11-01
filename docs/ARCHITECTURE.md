# Forge Conversationalist architecture

## Current implementation: stacked PRs, not yet in main
PR #1 defines LiveKit WebRTC transport, a simple browser voice UI, native
LiveKit Agents STT/LLM/TTS and Silero/turn detection, provider selection for
LiveKit Inference, OpenRouter and hybrid configurations. It remains unmerged,
awaiting attended cloud audio testing.

This stacked PR adds an optional lightweight-agent integration layer without
altering voice transport or inventing a second agent runtime.

Browser -> LiveKit room -> LiveKit AgentSession (one room-scoped assistant)
                                  |               |
                  approved MCP Toolset        delegate_to_hermes
                       |                           |
             authenticated gateway            Hermes Runs API
                    |                         API-owned session
               existing producers

## MCP and safety contract
- Disabled when MCP_URL is absent; no implicit "full tool" discovery.
- Trusted worker configuration provides URL, bearer token and nonempty exact
  `MCP_ALLOWED_TOOLS`. MCPServerHTTP is pinned to Streamable HTTP; allowed
  tool names are passed to LiveKit's native server filter.
- The credential and allowlist belong to the backend, never to a browser
  participant or LLM prompt. Plain HTTP is allowed only on loopback.
- **An MCP allowlist hides tools from the agent but is NOT authorization.**
  The MCP gateway itself must independently check identity, scope, permissions,
  approvals and consequential actions; robots stay behind Puppeteer and SDK
  safety. Platform ContextForge remains a source-controlled development
  foundation, not a deployed production endpoint.
- Read-only tool grants are the advised first profile; no production write or
  physical capability is enabled in this repository.
- No duplicate gateway, arbitrary tool execution, agent tool registry or
  robot driver is implemented here.

## Hermes delegation contract
- Entirely optional (HERMES_DELEGATION_ENABLED=false by default).
- Uses Hermes' documented authenticated `POST /v1/runs` (with idempotency key)
  and `GET /v1/runs/{id}` for progress and final output. No Hermes model
  runner, memory or session database is copied into Conversationalist.
- Each LiveKit room receives a distinct Hermes **API-owned** session ID.
  Only run IDs submitted by the current room may be checked. No API attempt
  to attach to a CLI/TUI/ACP-owned Hermes session (that requires the Hermes
  authenticated gateway WebSocket in later dedicated primary-mode work).
- Delegation is an *admission*, not a completion. The lightweight assistant
  can check a run on request and speak actual returned status/output. It
  does not claim background push notifications, approvals via voice, or
  automatic recovery of unknown outcomes.
- No automatic retries after ambiguous transport errors. Hermes executes
  only with the independently configured API key and its own tool privileges;
  for safe deployments grant a constrained Hermes tool profile.
- API keys and MCP bearer tokens stay in the Python worker environment.
  No provider or gateway key goes into the LiveKit room token or browser.

## Explicitly deferred
- Hermes-primary interactive session, streaming run events, approvals,
  and automatic quiet-break completion announcements.
- Multi-microphone or multi-agent arbitration.
- Live ContextForge authorization/security acceptance and connected robotics
  hardware/physical execution.
- Production user identity, authorization, and secrets provisioning.

No real credentials, remote MCP server, local Hermes process or robot device
was used during CI. Those checks require local Codex acceptance before merge.
