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
and speaker routing depend on browser/OS support). A local audio CLI can also
join the same LiveKit room as a participant. It uses the host's system
microphone and speakers by default, and allows an explicit output-device
override when the host exposes one. This supports browserless local voice use
while keeping the agent's room, VAD, STT, TTS and turn handling in LiveKit. It
does not start a second robot motion controller.
The local speaker's sounddevice playout is not acoustic feedback. Existing
worker `playback_presence` timestamps continue to describe the agent's outgoing
LiveKit track, not when the host speaker becomes audible.
When the opt-in native ensemble hook is attached, structured worker logs
preserve semantic state receipt and LiveKit output start/progress/finish
timestamps with wall-clock and monotonic receipt times. These records can be
correlated with Puppeteer action IDs and journal timestamps, but they remain
server output evidence rather than browser acoustics or observed physical
motion.

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

## PR2 native room authority and shutdown

The existing Puppeteer MCP child now consumes independently reviewed
legacy/native_v1/smooth_v2 profile selection from an operator-owned config,
after read-only preflight and an exact advertised performer/behavior catalog.
Optional TTS-frame observation is content-free, attributed to each performer
and runs through the **same** MCP child. The LiveKit audio pipeline remains
unchanged; a generated PCM timestamp or LiveKit playback callback does not
verify a real speaker or physical movement.

On signed participant departure, LiveKit room loss or worker shutdown, the
same `EnsembleSession` fences new cues, prioritizes an interrupted
room presence ahead of old feature queues, requests Puppeteer's
bounded disconnected/STOP lifecycle, and emits an explicit
`forge.ensemble_shutdown.v1` acknowledgement/uncertainty record.
Actual neutral convergence, motor torque and sleep remain unknown unless
separately attended and measured. There is no parallel controller or
automatic neutral/sleep recovery. Puppeteer PR #20 remains independent.

## Deferred
A Platform-owned authenticated communication feed, scoped event fanout,
room-origin filtering, alert preferences, privacy policy,
additional approved display/light renderers, multi-participant microphone/speaker
arbitration and live hardware tests. Opt-in content-free speech features now
flow to Puppeteer's authorized profile in software, but speaker alignment
and real robot movement remain unqualified.
