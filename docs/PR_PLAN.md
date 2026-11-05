# Mostly linear PR program

PR #1 (merged): LiveKit room, no-robot browser UI, OpenRouter/LiveKit/hybrid
inference profiles, basic voice-worker tests. Provider compatibility
and microphone-to-speaker latency still require attended cloud validation.

PR #2 (merged): optional authenticated, allowlisted MCP toolset and
API-owned Hermes Runs delegation. Real gateway/Hermes integration remains
disabled by default pending attended validation.

PR #3 (merged): device-independent presentation signals, optional
status chimes and emotion vocabulary, browser microphone/speaker selectors,
robot-free UI previews, tests and documentation. No Platform subscription.

PR #9 (merged): initial signed LiveKit room with one Puppeteer
semantic ensemble MCP child. Later merged PRs extended v2 Reachy-only,
v3 Reachy+StackChan, playback presence, and generated-PCM envelopes.

Puppeteer PR #40 (merged into Puppeteer main at
`979da719834b1e04641097555eb017c40d55f011`):
independently selectable opt-in native/smoothed idle and speaking profiles,
existing SDK-only execution, stale feature and unknown STOP quarantine,
CI regression coverage. Hardware remains unqualified.

Conversationalist PR #31 (this dependent software PR): operator
profile preflight, exact startup behavior catalog, high-priority barge-in,
single room-owned MCP child, bounded disconnect outcome, signed owner
departure, no automatic neutral/sleep/rearm, simulation tests and docs.
No physical or local-speaker acceptance is claimed by this PR.

Exact migration of stock 4G business prompts/tools remains separate.

Future coherent changes after PR #3:
- Authorized consumer adapter for Platform's future communications bus.
  Publisher/subscriber identity, privacy, delivery/replay policy and
  user preferences are **Platform responsibilities**, not new runtime code here.
- Canonical presentation emitter integration with LiveKit browser clients and
  other registered endpoints with verified message origin.
- Additional Puppeteer-approved indicator and expressive device renderers
  only after physical review; current software profiles are opt-in.
- Full Hermes interactive session continuity and multiple participants/
  input arbitration, separately qualified.
- Cross-agent operational observability through Platform OpenTelemetry. Add
  privacy-safe spans/events for agent tool invocations and outcomes, including
  admission, completion, failure, timeout/unknown and cancellation/STOP status;
  correlate Conversationalist call IDs with Puppeteer action IDs and preserve
  Platform's durable evidence/event ownership. Do not export tool arguments,
  prompts, transcripts, audio, credentials or raw robot coordinates. Treat OTel
  as a monitoring projection, not the authoritative action journal. This is a
  planned cross-repository slice; no end-to-end agent-to-Platform exporter or
  collector acceptance is claimed yet.
- Puppeteer attended shutdown acceptance: investigate abrupt session endings
  that leave motor noise or an unconfirmed native pose. Current adapter teardown
  requests STOP but Reachy's `goto_target` has no independent stop acknowledgement;
  the adapter has startup wake-up and captured-neutral return, but no approved
  sleep operation. The current daemon is configured to persist after room close
  and is launched with `--no-goto-sleep-on-stop`, so room close does not invoke
  the daemon's sleep-on-stop path. Define a reviewed shutdown lifecycle and
  evidence contract before another physical trial. Do not equate a longer delay,
  quiet observation, SDK task return or host cancellation with confirmed
  sleep/settling, and do not auto-retry an unknown action.

PRs already merged are not claimed to have passed remote provider or
physical hardware tests. Main contains reusable code, not a live deployment.
