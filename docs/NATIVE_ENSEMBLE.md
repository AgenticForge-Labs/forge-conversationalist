# Native Puppeteer ensemble — Conversationalist PR 2

**Software state:** dependency [Puppeteer PR #40](https://github.com/AgenticForge-Labs/forge-puppeteer/pull/40)
is **merged** into its `main` at
`979da719834b1e04641097555eb017c40d55f011`.
Conversationalist PR #31 is the dependent room-lifecycle and software
preflight change. Installation must use a verified merged GitHub `main` commit,
not an unmerged feature branch.

**Safety:** This is **software-only** integration. No sample, CI test,
MCP acknowledgment or speaker-track callback establishes measured physical
motion, head clearance, sleep, stopped torque, settled servo motion or
actual speaker/acoustic synchronization. Puppeteer PR #20 (neutral-return)
remains separate and hardware-gated. Do not retry or rearm an UNKNOWN STOP.

## Ownership and profiles

```text
LiveKit signed room + authorized participant
    -> Conversationalist (voice, turn state, outbound PCM analysis)
       -> ONE EnsembleSession + stdio MCP child
          -> Puppeteer ConversationEnsemble / ExecutionRuntime
             -> reviewed Reachy Mini SDK controller
             -> reviewed StackChan Control SDK controller
             -> optional reviewed SO-ARM101 approved program executor
```

No other robot controller, audio output sink, microphone reader, Pollen
Conversation App motion owner or credential-bearing subagent is created.
Conversationalist delegates content-free state and finite gesture intent;
Puppeteer owns actuator SDK use, resource reservation and STOP. SO-ARM101
never converts audio features to inferred joint trajectories.

Use a **single** operator-owned `PUPPETEER_ENSEMBLE_CONFIG` JSON file,
selected in the trusted Python worker (never in the browser). Puppeteer
supports these exact schemas:

- v1: Reachy, StackChan and SO-ARM101; SO-ARM simulation is the default.
- v2: Reachy only, no StackChan or SO-ARM.
- v3: Reachy + StackChan, with no SO-ARM SDK opened.

The operator config retains explicit robot identity, reviewed neutral
head/antenna references, actual device grants, SDK/firmware versions,
asset pins, action journal and independent ownership DB. The file's SHA-256
is checked immediately before creating the child.

The optional `motion_profiles` field supports independent
`idle`/`speaking` selections for each native performer:

```json
{
  "speech_driven_performers": ["reachy", "stackchan"],
  "motion_profiles": {
    "reachy": {"idle": "native_v1", "speaking": "smooth_v2"},
    "stackchan": {"idle": "smooth_v2", "speaking": "native_v1"}
  }
}
```

This is an **illustrative subset** of a larger operator-reviewed v3
configuration; it is **not runnable alone** and conveys no permission.
The default for each omitted profile remains `legacy`.
`native_v1` selects existing device-native-inspired authored behavior.
`smooth_v2` idle selects bounded lower-amplitude authored targets;
`smooth_v2` speaking additionally requires explicit
`speech_driven_performers` and per-robot envelope smoothing. Reachy
nonlegacy profiles require reviewed antenna neutrals and the
`pose_and_antennas` grant. StackChan speaking uses SDK-native mouth
weight/face semantics; SO-ARM continues approved finite programs only.
No agent or browser can elevate these grants.

## Install the merged dependencies

Use the **exact merged Puppeteer** software state rather than historical
PR #13 or PR #25 examples:

```bash
python -m pip install 'forge-puppeteer[mcp,reachy] @ git+https://github.com/AgenticForge-Labs/forge-puppeteer.git@979da719834b1e04641097555eb017c40d55f011'
python -m pip install 'stackchan-control[serial] @ git+https://github.com/AgenticForge-Labs/stackchan-control-sdk.git@08c25c919615ecc28e56b25c157bdcb29155d202'
# Only for a v1 configuration with SO-ARM:
python -m pip install 'soarm101-motion-sdk @ git+https://github.com/AgenticForge-Labs/soarm101-motion-sdk.git@60589ef56e02084ff53e432771d0cc7b7d257e1c'
```

Confirm SDK/firmware compatibility and actual installed entrypoints
locally. The versions above identify the Puppeteer PR 1 CI dependencies;
they do **not** certify the attached devices.

## Nonmoving operator preflight — before LiveKit or hardware

Run from the Conversationalist Python environment:

```bash
python -m forge_conversationalist.ensemble_preflight \
  --config /absolute/path/to/operator-approved-ensemble.json
```

Or use `forge-conversationalist-ensemble-preflight --config ...` after
installing the console script.

The preflight parses the reviewed JSON, verifies exact schema/performer
selection and profile names, requires expected native SDK grant markers,
checks the referenced Reachy neutral file's 4x4 structure and
`REVIEW_REQUIRED` placeholders, fingerprints the config, and prints
JSON with versioned `forge.ensemble_preflight.v1` evidence.
It does **not** connect to any SDK, robot, daemon, serial port, MCP child,
LiveKit service or speaker. `static_config_valid` is **not** a hardware
readiness claim. The real Puppeteer launcher and adapters still validate
all actual hardware authority, workspace/geometry, firmware and replay
assets before/at each dispatch.

Configure the trusted worker only:

```bash
PUPPETEER_ENSEMBLE_CONFIG=/absolute/path/to/operator-approved-ensemble.json
# Optional only if the executable is not on PATH:
PUPPETEER_ENSEMBLE_COMMAND=/absolute/path/to/forge-puppeteer-ensemble
# Both disabled by default; operator opts in to one or both:
CONVERSATIONALIST_PLAYBACK_PRESENCE=1
CONVERSATIONALIST_OUTGOING_SPEECH_ENVELOPE=1
```

Never set the legacy restricted `PUPPETEER_MCP_CONFIG` or fake
`PUPPETEER_EMBODIMENT_CONFIG` simultaneously; both create competing robot
owners. Voice-only mode requires none of these.

## Signed-room runtime and capability preflight

For Reachy `smooth_v2` speaking, the operator may additionally set
`reachy_legacy_speech_fallback: true` in the reviewed Puppeteer configuration.
If fresh generated-speech features stop arriving, Puppeteer can then use the
reviewed classic head-only speaking rhythm until fresh features return. This
rhythm is not synchronized to the audio. It does not retry an uncertain motion
or override interruption, and it requires classic Reachy expression style and
the existing explicit speech-driven performer grant. This option has not been
physically qualified.

The worker connects its authenticated LiveKit room **before** launching the
one optional Puppeteer stdio child. It requires job-metadata
`authorized_identity`, a stable room name and an operator-unchanged config.
The child receives **no** LiveKit/OpenRouter/Hermes/Platform environment
secrets. On MCP initialization, required tools are
`ensemble_state`, `ensemble_presence` and
`ensemble_expression`; the optional `ensemble_speech_envelope` feature
is negotiated independently.

Before sending any motion-inducing room state, the worker requires a
`forge.behavior.v1` disconnected snapshot, no foreign session, exactly the
operator-selected performer IDs, per-robot behavior revision
`sha256:<64 lowercase hex>`, an explicit expression list and an
idle/listening/speaking capability list. Absent/malformed catalogs,
unexpected performers, blocked/unavailable devices or broken child
handshakes fail closed to **voice-only**. These returned catalog revisions
are source-authored identity/evidence, not user permission, and cannot
guarantee physical clearance or sleep.

The room sends content-free, increasing-sequence approximate
`agent_state` presence and ~10-second heartbeats via the existing MCP
queue. Optional `CONVERSATIONALIST_PLAYBACK_PRESENCE=1` listens to
LiveKit's **existing** audio-output start/progress/finish callbacks after
`AgentSession.start`; those events describe samples published toward
the LiveKit track, **not** remote speaker playout. A barge-in is an urgent
interrupted listening state. It prevents stale playback start callbacks
from rearming speaking.

Optional `CONVERSATIONALIST_OUTGOING_SPEECH_ENVELOPE=1` wraps the
**existing** default TTS node as a pass-through; original audio frames
are yielded unchanged to LiveKit. Only bounded content-free
`forge.speech_envelope.v1` 50 ms hops (at most eight per MCP call,
four per offer by default) cross the bridge. One offered batch is
attributed independently to each configured performer. Stream
backpressure disables further observations rather than retrying gaps.
An interruption/drop fences queued old speech batches and prioritizes
the later state; new TTS streams must start afresh. Puppeteer alone decides
whether its separate *operator-approved* speaking profile may turn those
latest features into SDK-validated motion. No PCM, transcripts, prompts,
LLM tokens or private account content reach the robot.

## Ordered interruption and bounded shutdown

Urgent interrupted/listening presence and room closure overtake unsent
optional envelope work. Any in-flight optional envelope MCP call has a
350 ms timeout, after which that performer's feature stream is disabled;
optional feature transport must not wait for the full 15-second semantic
action timeout. Pending older speaking states are superseded;
queued gestures canceled by a true interruption are reported denied
before dispatch. This cannot rewind an action **already** admitted to the
SDK; only Puppeteer's STOP/quarantine evidence controls that outcome.
A denied/unconfirmed presence or action fences subsequent motion requests;
no silent retry, automatic rearm or fallback to another controller.

Three paths request **the same** `EnsembleSession.close()`: owning worker
shutdown, signed authorized participant departure and LiveKit room
disconnect. Owner rejoin does **not** auto-restart hardware. Shutdown
sends one bounded `ensemble_presence(state="disconnected")` and
records a `forge.ensemble_shutdown.v1` JSON result with per-robot status,
an MCP disconnect acknowledgment when available, and clear separate
`neutral_request: not_attempted`,
`sleep_request: not_attempted`,
`physical_settling: unconfirmed` fields. An `acknowledged`
disconnect is *not* proof of a physically settled robot. An
`acknowledged` result additionally requires one `idle` software status
for **every** operator-selected performer in the returned Puppeteer snapshot;
missing/unknown/blocked/unavailable statuses cannot be treated as a clean
shutdown. If MCP/STOP is unknown, the room remains
fenced from reconnecting until operator reconciliation.
**No automatic neutral or sleep is issued:** Puppeteer PR #20 has not
qualified that transition or obtained independent physical settling
evidence. A previously UNKNOWN Reachy target must not be retried.

The browser's authorized `forge.set_robot_connection` RPC reflects an
uncertain disconnect as `unknown`, rather than claiming a successful
stop. The `forge.get_robots` and expression RPCs still use the same
Puppeteer catalog, explicit performer selection and signed participant
identity.

## Software tests and attended handoff

```bash
python -m pip install -e '.[dev]'
ruff check src tests
pytest -q
python -m compileall -q src
cd web
npm install --no-audit --no-fund
npm run check
npm run test:voice-ui
npm run build
```

CI runs Python 3.11 and 3.12 plus the web type-check, voice UI and build.
The tests cover no-robot fallback, v2/v3 authorization, stored config digest,
operator preflight, real stdio **fake** child, repeated room voice states,
interruption and queued old PCM, dual-performer feature streams, capability
mismatch, missing/unavailable child, and unknown STOP shutdown. They open
**no** real hardware.

**Attended local Codex acceptance remains deferred until one reviewed
session.** Use the merged SHA from both Puppeteer and Conversationalist.
Confirm exact grants and installed SDKs, distinct serial/daemon owners,
Reachy unknown STOP reconciliation, actual neutral and antenna clearance,
speech-to-motion timing against the local EMEET speaker, StackChan
screen and head limits, interruption under actual motion, watchdog
behavior, physical stop/settling, daemon persistence and safe sleep.
Keep PR #20 separate unless independently qualified; an MCP response
alone cannot prove those observations.

Historical Puppet PR #13/Conversationalist PR #9 are already merged;
their older SHA install examples are obsolete. Do not regress the
no-robot, older restricted Reachy or fake-only modes.
