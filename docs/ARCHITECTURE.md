# Forge Conversationalist architecture

## Integrated main (PRs #1 and #2)
LiveKit owns rooms, microphone/audio tracks, TTS playback and turn
detection. The worker selects independent LiveKit/OpenRouter/hybrid STT,
LLM and TTS models and provides a lightweight voice agent. Hermes is
an opt-in API-owned task-delegation backend; it owns all actual agent
runs and memory. Native LiveKit MCPToolset can connect to an authorized
MCP server with exact tool grants. Platform owns its internal records and the **future cross-system communication
bus** (subscriber authentication, filtering, replay and notification delivery);
producer systems retain their original domain facts. Puppeteer owns
validated physical performance; SDKs own
hardware motion and safety. Cloud and physical integration validation
remains outstanding; nothing here asserts deployed production readiness.

## Device-independent presentation (PR #3)
Audio input/output is attached to a **LiveKit participant**, not a robot.
The browser offers microphone and speaker selectors (device availability
and speaker routing depend on browser/OS support). A future Reachy or
other physical audio client should join the same LiveKit room through
its owning SDK media bridge without making it the agent's microphone
or speaker by default.

Small semantic presentation signals are independent of the microphone
and speaker:
- Presence: idle/listening/speaking.
- Emotions: neutral/happy/curious/confused/concerned/excited.
- Notifications: new mail/order/failure indicator with an optional
  chime and generic visual label.

The canonical Python `PresentationEvent` -> `PresentationCue` mapping
in `presentation.py` is deterministic, provider-free and robot-free.
The browser includes only manual preview controls and a separate LiveKit
voice-state readout. `publish_cue` is an optional authorized-emitter
utility, not yet wired to a Platform event subscription or web client.
No real provider event, notification service, data queue, or physical
expression is implemented by PR #3. See
[Presentation Signals](PRESENTATION_SIGNALS.md) for wire formats,
privacy/sender verification and renderer responsibilities.

## MCP and Hermes safety
MCP tools remain opt-in and require trusted backend URLs, bearer grants
and exact allowlists; backend authorization cannot be replaced by tool
filtering. Hermes Runs API is an admission-and-status integration, not
a duplicate agent runtime or takeover of an existing CLI/TUI session.
Secrets never enter browser code. No status cue or LLM prompt can
authorize an action against a company account or physical device.

## Native three-performer voice bridge (PR #9)

The optional `EnsembleSession` is a single room-owned MCP stdio client
that consumes approximate LiveKit state callbacks. It sends only ordered
content-free idle/listening/speaking states and an explicitly requested
bounded named gesture; it never processes audio, speaks through a robot
speaker, or opens an SDK device directly. Puppeteer owns approved recorded
and procedural expression, scheduling, resource arbitration and hardware
STOP/HOLD. It uses a separate operator-reviewed config and is mutually
exclusive with the existing fake embodiment and restricted Reachy clients
to prevent conflicting controllers. An authorized LiveKit room and stable
signed identity are required before the native SDK child starts.
See [Native Ensemble](NATIVE_ENSEMBLE.md). 4G persona migration and
physical output timing are still separate acceptance work.

## Deferred
A Platform-owned authenticated communication feed, scoped event fanout,
room-origin filtering, alert preferences, privacy policy,
real display/light/robot renderers, multi-participant microphone/speaker
arbitration and live hardware tests. No amplitude-reactive robot motion
is desired or planned.
