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

PR #9 (current software PR 5/5 of the Puppeteer integration): connect
one authorized LiveKit room to the merged Puppeteer PR #13 native SDK
three-performer semantic ensemble. No new LLM, provider or physical motion
authority; no measured voice timing or hardware qualification. Exact
migration of legacy stock 4G prompts/business tools is separate.

Future coherent changes after PR #3:
- Authorized consumer adapter for Platform's future communications bus.
  Publisher/subscriber identity, privacy, delivery/replay policy and
  user preferences are **Platform responsibilities**, not new runtime code here.
- Canonical presentation emitter integration with LiveKit browser clients and
  other registered endpoints with verified message origin.
- Optional Puppeteer-approved indicator and expressive device renderers,
  after its independent talking/idle and SDK work is integrated.
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
