# One LiveKit conversation across three native Puppeteer performers

**Implementation:** Conversationalist PR #9 (PR 5/5 of the integration plan).
**Dependency:** merged [forge-puppeteer PR #13](https://github.com/AgenticForge-Labs/forge-puppeteer/pull/13),
merge SHA `a3b878bca5264ad40c03478027f94f67fc1a0808`.

This is a **software-only conversation/event bridge**. Do not equate it
with qualified SO-ARM physical motion, Reachy recorded movement, StackChan
USB control, or measured voice-to-motion sync.

## Ownership

```text
One Conversationalist voice session in LiveKit
  existing LLM/prompt, STT/TTS and optional MCP/Hermes tools
    |
    | agent_state (approximate idle/listening/speaking)
    | explicit operator/user requested named gesture
    v
One Puppeteer room-owned stdio MCP child
  ensemble_state
  ensemble_presence
  ensemble_expression
    |
    v
Puppeteer: one ConversationEnsemble/ExecutionRuntime
  -> Reachy Mini native SDK
  -> StackChan Control SDK
  -> SO-ARM101 native SDK prevalidated choreography + watchdog
```

Conversationalist does **not** import any robot SDK, create a second
motion controller, run a Pollen Conversation App instance, or send
transcripts, PCM, LLM tokens, prompts or company data to Puppeteer.
4G/personality prompts and source-authorized business tools must be
configured in the existing conversation owner; no hardware-specific 4G
personality is installed or copied by the bridge. The legacy
`forge-reachy` stock-Conversation-App deployment remains distinct
until attended replacement proves feature parity.

## Operator installation and motion authorization

Install the **exact merged** Puppeteer commit into the worker's trusted
environment, including the native SDK dependencies described in
[Puppeteer three-robot ensemble instructions](https://github.com/AgenticForge-Labs/forge-puppeteer/blob/main/docs/THREE_ROBOT_ENSEMBLE.md).
For example:

```bash
python -m pip install 'forge-puppeteer[mcp,reachy] @ git+https://github.com/AgenticForge-Labs/forge-puppeteer.git@a3b878bca5264ad40c03478027f94f67fc1a0808'
python -m pip install 'soarm101-motion-sdk @ git+https://github.com/AgenticForge-Labs/soarm101-motion-sdk.git@60589ef56e02084ff53e432771d0cc7b7d257e1c'
python -m pip install 'stackchan-control[serial] @ git+https://github.com/AgenticForge-Labs/stackchan-control-sdk.git@391ccb9b5ff777fbd88df6a97e2477161a34c7ec'
```

Puppeteer's own review-only generator can produce a candidate **simulation**
configuration with seven full-path SHA-pinned SO-ARM programs around a taught
neutral. It deliberately writes `REVIEW_REQUIRED` approval markers and does
not connect to hardware. The operator must review each device and path,
independently approve the actual native SDK policy, and maintain one
absolute, private operator-owned JSON config file.

In Conversationalist's root worker `.env.local`, not `web/.env.local`:

```bash
PUPPETEER_ENSEMBLE_CONFIG=/absolute/operator-reviewed/three-robots.json
# Optional when the exact installed executable is not on worker PATH:
PUPPETEER_ENSEMBLE_COMMAND=/absolute/path/to/forge-puppeteer-ensemble
```

No mode is enabled without `PUPPETEER_ENSEMBLE_CONFIG`. It requires
Puppeteer's explicit `I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION` marker and
three unique operator-reviewed performer identities. The config is
SHA-256 fingerprinted when the worker loads it and checked again before
the MCP child starts. The child receives no LiveKit, Hermes, OpenRouter,
business, or Platform secrets. The Puppeteer service additionally
validates the entire exact approval list, host/SDK connection settings,
workspace, physical calibration, and watchdog. Conversationalist does
not accept a tool parameter to change these.

The matching Puppeteer Reachy-only configuration (`schema_version: 2`)
contains a single native Reachy identity and no StackChan or SO-ARM fields.
The worker checks that exact identity at discovery; it does not fabricate
the missing performers or grant them physical authority. This requires the
Puppeteer release that implements the corresponding version 2 launcher.

Do **not** set `PUPPETEER_MCP_CONFIG` or
`PUPPETEER_EMBODIMENT_CONFIG` in the same worker deployment while
native ensemble mode is enabled; this is rejected so that an
overlapping Reachy/robot action owner cannot be started for one room.

## Runtime behavior

An authorized room must have a stable LiveKit room name and a signed
`authorized_identity` from trusted server job metadata. Without both,
Conversationalist provides voice without starting the physical
ensemble or advertising its gesture tools.

The worker connects to the LiveKit room **before** opening the optional
Puppeteer SDK child. At startup, the child must advertise the exact
three named MCP tools, the `forge.behavior.v1` schema, an unoccupied
room and matching performer identities.

If this optional child cannot start or fails discovery, the worker logs the
failure and starts voice without ensemble instructions or robot tools. It
does not retry or rearm the performer; the operator must reconcile any
unknown physical action before a later attempt.

The state hook sends one monotonically sequenced, content-free
`ensemble_presence` event for each LiveKit state change and refreshes
the latest state approximately every 10 seconds. The source is
`agent_state`: a best-effort LiveKit state callback, **not**
independently measured speaker output start, output drain, phoneme
alignment or actual device motion. Puppeteer's own session TTL and
performer arbitration handle gaps and denied events. An unavailable
device is visible independently; the worker must not claim all three
physically followed a cue.

When a user-speaking callback arrives while the hook still reports the
agent as speaking, the listening event is marked interrupted so Puppeteer
uses its immediate interruption path. Callback ordering can prevent this
overlap from being observed; it is not a measured audio interruption or
evidence that a physical STOP has completed.

The LLM may read `get_ensemble_status`; it can use
`request_ensemble_gesture` **only if the user explicitly asks for**
a named finite expression on one selected performer. The names are obtained
from Puppeteer's per-performer catalog rather than an application-wide
hard-coded allowlist. Reachy can expose reviewed yes/no and pinned emotions;
StackChan and SO-ARM have independent available programs. Ordinary
speaking/listening/idle runs deterministically through the LiveKit
state hook without agent tool calls. A new expression gets a unique ID
and is never automatically retried on an ambiguous outcome. Accepted
MCP admission is not movement completion. Shutdown attempts
`disconnected`; a missing acknowledgement is logged, not interpreted
as STOP/HOLD.
An accepted gesture response explicitly reports completion as unconfirmed;
the assistant must not claim that it happened or that a blocked robot will
settle or resume by itself.

## Tests and attended acceptance

CI must pass:

```bash
python -m pip install -e '.[dev]'
ruff check src tests
pytest -q
python -m compileall -q src
cd web && npm install --no-audit --no-fund
npm run check && npm run test:voice-ui && npm run build
```

The offline fixture starts one genuine MCP **stdio** server with fake
typed `ensemble_state/presence/expression`, asserts ordered 3-performer
events, exact names, bounded gestures, shutdown, and that OpenRouter
credentials are absent from the child. It opens no hardware. Existing
fake-only embodiment and restricted Reachy are preserved and
independently tested.

**End-of-series local Codex / attended physical gate** (not run by CI):
verify exact main SHAs and the reviewed config; one 4G/voice session,
three independent SDK identities, no concurrently running Pollen
motion owner, LiveKit device selection, latency and speech-to-head
timing, startup/reconnect failure, no unauthorized room motion, correct
gesture rendering, physical stop authority and post-STOP travel,
watchdog/lease expiry, and full trace of denied/busy/unknown outcomes.
On SO-ARM101 test calibrated, operator-held neutral first, with physical
power isolation; do not enable an unqualified path because simulation
passes. Record actual audio-output and motion timestamps to make
any future synchronization claim.

A separate follow-on migration is required for the stock 4G app's exact
personality/business tools and any real output-device playback hooks.
The semantic bridge by itself cannot claim that those have migrated.


## Browser robot-control transport

Puppeteer PR #15 adds immutable per-robot `expressions`,
`presence_behaviors` and `behavior_revision` to the existing
`ensemble_state` snapshot. `forge.get_robots` presents this catalog
read-only to the signed LiveKit room participant.
`forge.request_robot_expression` accepts only
`{"performer_id": "...", "expression": "..."}` from the same authorized
participant, and uses `EnsembleSession.express` and the same room-owned
MCP child as natural presence. It re-checks catalog support before dispatch,
then targets only that performer. It does not expose an arbitrary SDK
tool or a second robot owner.

These controls expose **all finite behaviors approved in the currently
installed per-robot pack**. They do not automatically enable unreviewed
official Reachy recordings or dances, unavailable StackChan firmware
capabilities, or unqualified SO-ARM choreography. Automatic
idle/listening/speaking are shown as presence indicators, not manual
gesture buttons.

An action response may show `accepted` and `completion: unconfirmed`.
For real confirmation, inspect subsequent action evidence and hardware
observations. A timeout does not authorize a retry. Real LiveKit RPC,
exact user permission, web layout and robot hardware require local
acceptance after CI.


## Experimental LiveKit output-playback presence (opt-in)

Conversationalist can now use LiveKit's own **output** lifecycle events
rather than only its model/agent state to start and finish the robot's
`speaking` presence. Enable on an already-authorized worker:

```bash
CONVERSATIONALIST_PLAYBACK_PRESENCE=1
```

No setting is required when using voice without a robot; the default is
still legacy coarse `agent_state` presence. This is **not** a second
audio player, robot driver, motion scheduler, browser endpoint, or alternate
authorization path.

After `AgentSession.start`, the worker subscribes to the existing
`session.output.audio` events:

- `playback_started`: sends `ensemble_presence(state="speaking",
  source="playback")` through the one room-owned Puppeteer MCP child.
- `playback_finished`: returns to the latest compatible semantic state,
  using `source="playback"` and preserving the interruption flag.
- User interruption: immediately stops reporting speaking, with
  `source="agent_state"` and `interrupted=True`. Stale late output events
  cannot silently restart robot motion before a fresh agent-speaking event.
- If an output sink is missing/incompatible, the worker warns and preserves
  the previous semantic-only mode. No automatic rearm or retry occurs.

When this opt-in hook is attached, the worker also writes content-free
structured timing records for semantic state receipt and LiveKit output
`playback_started`, `playback_progressed`, and `playback_finished` callbacks.
Records include the room ID, source event time when supplied, audio offset and
duration when supplied, worker receipt wall-clock and monotonic timestamps, and
the presence sequence/event ID with MCP call and response times. They contain
no PCM or transcript. Correlate the room ID and sequence with Puppeteer's
action IDs and action journal to measure state-to-submission and
submission-to-SDK-completion intervals. An MCP `accepted` response is
admission evidence only. The output timestamps describe LiveKit's server-side
output path; they do not establish browser or speaker acoustics, and must not
be reported as measured physical motion timing.
Preserve the worker logs alongside the Puppeteer action journal for attended
timing acceptance.

Important precision: the **default LiveKit RoomIO output** fires playback
events when audio is pushed toward the LiveKit track. This is *not a
measurement of the listener's network playout, browser speaker output,
acoustic waveform, physical robot position or hardware stop*. The returned
source indicates event provenance, not accurate synchronized PCM timing.
Periodic heartbeats retain the provenance of their last presence state.

No audio samples, transcript tokens, voice models or user speech are sent
to Puppeteer. The native semantic behavior remains a finite pre-authored
loop, and the physical qualification gate remains mandatory. The following
independent work is still outstanding: capturing bounded outgoing PCM
envelopes with validated playout timing; deriving safe speech offsets using
the pinned Reachy `speech_tapper` or equivalent; and composing them under
the one existing SDK motion owner without racing the recorded gestures.


## Optional outgoing TTS envelope preview

A separate opt-in CONVERSATIONALIST_OUTGOING_SPEECH_ENVELOPE=1 observes outgoing
16-bit synthesized PCM through LiveKit Agent.default.tts_node and passes
original AudioFrame objects unchanged to the only existing LiveKit output.
RMS/VAD analysis is done **once** in Conversationalist. A versioned,
content-free 50 ms feature stream travels over the existing authorized room
MCP connection via its optional ensemble_speech_envelope tool. The Puppeteer
receiver stores read-only presence-independent preview and per-performer
dimensionless channels; it does not actuate motors.

The bridge is explicitly best-effort; missing SDK/tool, format mismatch,
backpressure or connection failure must not alter audio output and never
triggers a motion retry. This does not establish EMEET/browser physical
playout times, neutral/sleep settling, or authorization for audio-reactive
hardware. No microphone samples, transcripts or raw PCM cross the MCP
boundary. Operator qualification and the independent PR #20 neutral-return
gate remain unresolved.
