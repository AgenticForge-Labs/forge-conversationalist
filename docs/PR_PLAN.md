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

PRs already merged are not claimed to have passed remote provider or
physical hardware tests. Main contains reusable code, not a live deployment.
