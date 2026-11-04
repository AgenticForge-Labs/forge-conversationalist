# Forge Conversationalist

## Development mail notifications through Hatchet Streams

The experimental `CONVERSATIONALIST_MAIL_STREAMS=1` feature runs **only** in
an authorized LiveKit room with an isolated development Hatchet token and
the matching `CONVERSATIONALIST_MAIL_STREAM_BUSINESS_ID`. The worker
subscribes to Platform `agenticforge.event.v1` `mail.received` events
and emits a private `forge.presentation.v1` mail indicator and the
**fixed generic question** “You've got new mail. Would you like a summary?”
No subject, sender, email body, or broker token reaches the room or a robot.
An affirmative answer uses the existing read-only company services/Hermes
tools, not an automatic mail read.

The separate `CONVERSATIONALIST_COMPANY_NOTIFICATIONS=1` opt-in additionally
recognizes exact, source-pinned `order.created`, `printer.job.failed`, and
`work.failed` notifications with existing generic `order`/`failure`
indicators and short nonprivate speech. Existing mail-only mode remains
unchanged. Finance and GitHub activity do **not** trigger unsolicited speech;
there is no implied approval to retrieve sensitive data, move robots or
take operational action. Shopify order/work failure publishers still require
separate source-linked rollout and provider acceptance.

`pip install -e '.[mail-streams]'` or `uv run --with 'hatchet-sdk>=1.44,<2'`
installs the optional Streams SDK; ordinary voice remains independent.
The feature also requires `APP_ENV=development`,
`HATCHET_CLIENT_TOKEN`, a signed room participant and an existing
company mail-read tool. It is **disabled by default**. It does not
automatically authorize Reachy motion or change the audio route; when Reachy
is the selected speaker it can voice the generic prompt using the existing
LiveKit output. A room starting mid-day deliberately skips historical
notices. Production replay/authorization still requires Platform's future
server-side subscriber API and durable cursor store.

To produce notices, run the explicitly selected read-only Zoho inbox watcher
from the Platform runbook `docs/live-mail-streams.md` against the **same**
shared development Hatchet engine. This integration has not received attended
live service or hardware acceptance.



A hardware-independent voice conversation app for AgenticForge Labs,
built on LiveKit Agents. **Conversation needs no robot or local LLM.**

PRs #1 and #2 are merged into main: LiveKit/OpenRouter/hybrid speech
profiles plus optional authenticated MCP and Hermes delegation.
Local OpenRouter/LiveKit voice acceptance has been exercised; hardware
integration requires separate attended acceptance.

PR #3 merged device-independent presentation signals and optional browser
microphone/speaker selection. It does **not** add a Platform subscription,
robot motion, or a new realtime audio engine.

### 4G personality

Set `CONVERSATIONALIST_PERSONALITY=4g` in the worker environment for one
shared 4G conversation. This selects the 4G identity, company background,
speaking style and source/permission guidance; it does not grant tools or
enable any robot. The worker's existing authenticated MCP allowlist and
server-side grants still determine available company tools. Configure the
4G defaults to OpenRouter's HeyGen Voice Turbo (`heygen/heygen-voice-1-turbo`)
with `lucian`, requiring `OPENROUTER_API_KEY`; STT and LLM follow the selected
preset. Explicit TTS settings and authorized per-room voice selection can
override that default.

This is an adaptation of the stock profile for a device-independent LiveKit
session, not full feature parity: Reachy-specific tools, Business Engine
WorkItem adapters, notifications and the stock hosted voice remain separate.
The greeting deliberately does not assert that company services are healthy.

## Run the voice application

1. Create a LiveKit Cloud project. Copy `.env.example` to root `.env.local`.
   Supply `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` and a long
   random `CONVERSATIONALIST_ACCESS_TOKEN`.
2. Set `CONVERSATIONALIST_PRESET=livekit|openrouter|hybrid`. Add
   `OPENROUTER_API_KEY` to **root only** when an OpenRouter stage is selected.
3. Install the Python worker and model assets:

   ```bash
   uv venv --python 3.12
   uv pip install --python .venv/bin/python -e '.[dev]'
   .venv/bin/python -m forge_conversationalist.agent download-files
   .venv/bin/python -m forge_conversationalist.agent dev
   ```

4. Copy `web/.env.example` to `web/.env.local`. Supply LiveKit credentials
   and the **development access token** only, not OpenRouter/MCP/Hermes keys.
   Start `cd web && npm install && npm run dev`; visit http://localhost:3000.

For a loopback-only LiveKit development server, set both env files to
`LIVEKIT_URL=ws://localhost:7880`, `LIVEKIT_API_KEY=devkey`, and
`LIVEKIT_API_SECRET=secret`; start `livekit-server --dev` bound to loopback.
Set `CONVERSATIONALIST_TURN_DETECTOR_VERSION=v1-mini` in the worker env to run
the turn detector locally. This loads a roughly 244 MB model and uses VAD-based
interruption handling; the default cloud setup keeps LiveKit's automatic turn
detector and adaptive interruption selection.

### Browserless system audio

The browser is optional for local voice conversations. Run the LiveKit worker
in one terminal, then start the local audio participant in another. The local
participant uses the host operating system's default microphone and speakers;
it joins the same LiveKit room as the agent, publishes microphone audio, and
plays only the agent's audio track. The worker and local participant must run
on the machine whose system audio devices should be used.

```bash
# Terminal 1: worker, with the root .env.local loaded as usual
.venv/bin/python -m forge_conversationalist.agent dev

# Terminal 2: enumerate devices, then start a voice session
.venv/bin/forge-conversationalist-audio --list-devices
.venv/bin/forge-conversationalist-audio
```

To override a device, pass an enumerated numeric index or a unique name
substring. For example, if the operating system lists Reachy's USB speaker:

```bash
.venv/bin/forge-conversationalist-audio --output-device "Pollen Robotics Reachy Mini Audio"
```

An unavailable or ambiguous override fails before connecting. Leaving the
options unset always selects the system defaults. This route keeps the same
LiveKit worker, provider models, room, VAD and turn handling; it does not start
a second robot motion process. Hold `Ctrl+C` to end the local session and
delete its ephemeral room. The local LiveKit audio device helper enables AEC,
noise suppression, high-pass filtering and AGC for its microphone stream. The
speaker output is connected to AEC as its reverse audio reference to reduce
the microphone mistaking the agent's reply for a new user interruption.
The agent's speaking presence still follows its outgoing LiveKit track events;
those events do not measure the exact moment sound reaches the selected
speaker.

The CLI uses `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` from
the trusted root worker environment. Keep these credentials out of
`web/.env.local`. `CONVERSATIONALIST_ACCESS_TOKEN` remains the browser UI's
local access gate; the browserless CLI does not require or send it.
The **Microphone sensitivity** setting adjusts Silero's speech activation
threshold. Less sensitive may reject background sounds but can also miss quiet
speech; Standard keeps the SDK default. Changes apply to the next conversation.
The pause setting adjusts how long the turn detector waits after speech stops;
shorter pauses respond sooner, while longer pauses allow more thinking time.
It also applies to the next conversation.

Enter the development access token once. The server saves it in a 30-day
HttpOnly, same-site cookie and automatically connects on later visits. The
cookie is scoped to the local session API, checked with same-origin protection,
and is never readable by page JavaScript. Clearing site cookies asks for the
token again.

For a trusted workstation where anyone able to use the local browser may
start a conversation, local development can sign in automatically. Start the
web server **bound only to loopback**, with the flag set on that process:

```bash
CONVERSATIONALIST_LOCAL_AUTO_LOGIN=true npm run dev -- --hostname 127.0.0.1
```

Then open `http://localhost:3000`. This opt-in works only in development
for same-origin localhost requests. It bypasses the access-token prompt for
local users; never expose this server on a network or enable the flag on a
shared or untrusted workstation. LiveKit credentials stay on the server.

The OpenRouter preset defaults to HeyGen Voice Turbo with the `lucian` voice,
using mono PCM at 44.1 kHz. The model and voice can still be changed per room.
Closing the browser leaves the agent and its conversation in memory; reopening
the site resumes the same room and recent transcript. Explicitly ending the
conversation deletes that room. Conversation history does not survive a worker
or LiveKit server restart.

During a conversation, use the **Speech model and voice** controls to switch
OpenRouter TTS models and voices without leaving the room. The worker applies
the change through an authenticated LiveKit RPC from the room's authorized
participant. Prices and voice IDs come from OpenRouter's public catalog and
refresh every five minutes; model choices are checked by both the API and the
worker. The browser receives only catalog metadata and the LiveKit session
token. The OpenRouter key remains in the worker environment.

## Optional lightweight agent integrations

**MCP** is off by default. Enable with a *trusted*, authorized MCP URL,
scoped bearer credential and explicit tool-name allowlist in root
`.env.local`:

```bash
MCP_URL=http://127.0.0.1:4444/mcp
MCP_TOKEN=YOUR_SCOPED_MCP_JWT
MCP_ALLOWED_TOOLS=data_read,work_status
```

These are **illustrative names from Platform's fake development MCP child**,
not a promise the unified ContextForge gateway is running. Use exact tool
names from your actually configured gateway. LiveKit MCPToolset is responsible
for MCP client transport/discovery; the MCP gateway remains responsible for
all authorization, approvals and hardware safety. The local MCP URL is an
example, not an activated production service. Prefer read-only grants.

**Hermes delegation** is also off by default:

```bash
HERMES_DELEGATION_ENABLED=true
HERMES_API_URL=http://127.0.0.1:8642
HERMES_API_KEY=YOUR_HERMES_API_SERVER_KEY
```

Configure `API_SERVER_ENABLED=true`, `API_SERVER_KEY` in Hermes' own
settings and run `hermes gateway`. The API typically binds loopback port
8642; use a separate constrained Hermes profile because Hermes can have
powerful tools. No browser-to-Hermes connection is required.

Say \"Ask Hermes to research X\". Conversationalist invokes
`delegate_to_hermes` once, receives a run ID, and truthfully reports it
as *started*. Later say \"Check that Hermes task\" to invoke
`check_hermes_run`; it reports the actual status or final output.
There is no automatic task-completion announcement yet, no approval-by-
voice, and this does not attach to your existing TUI/CLI Hermes session.

### Optional restricted Reachy expression gateway

The Puppeteer stdio gateway is separately opt-in. Conversationalist starts
the installed `forge-puppeteer-mcp` launcher with an operator-owned config
file and exposes only explicitly selected `play_move`, `list_assets`,
`action_lookup`, and `device_stop` tools. All four are required. Each room's
stdio process must use the same shared `ownership_db` and `action_journal`
paths so Puppeteer can serialize actions and retain uncertain outcomes across
sessions. Puppeteer filters asset discovery to the exact operator-approved pins.
The gateway config must select
Reachy SDK restricted mode, declare physical authority, and pin at least one
approved asset ID and full-snapshot revision. Reachy pose and antenna targets
are rejected by this integration. The agent instructions require an explicit
user request before a move; it will not move automatically while greeting,
listening, speaking, or idle. Puppeteer's server-side asset and authority
checks remain authoritative.

```bash
PUPPETEER_MCP_CONFIG=/absolute/path/to/reachy-approved-moves.json
PUPPETEER_MCP_COMMAND=/absolute/path/to/forge-puppeteer-mcp
PUPPETEER_MCP_ALLOWED_TOOLS=play_move,list_assets,action_lookup,device_stop
```

`PUPPETEER_MCP_COMMAND` is optional when the launcher is on the worker's
`PATH`. For an isolated Puppeteer environment, set it to the absolute
executable path; Conversationalist verifies the executable bit and requires
the basename `forge-puppeteer-mcp`. For example, this workstation's separate
Puppeteer environment can use
`/home/larry/.local/share/forge-puppeteer/reachy-venv/bin/forge-puppeteer-mcp`.

The JSON file is configured by the operator in Forge Puppeteer. This example
is intentionally incomplete and will not connect:

```json
{
  "backend": "reachy_sdk",
  "device": "reachy",
  "physical_authority": "I_AUTHORIZE_REACHY_CONTROL",
  "restricted_actions": true,
  "allowed_tools": ["device_info", "device_state", "list_assets", "action_lookup", "play_move", "device_stop"],
  "ownership_db": "/var/lib/forge-puppeteer/reachy/ownership.sqlite3",
  "action_journal": "/var/lib/forge-puppeteer/reachy/actions.sqlite3",
  "approved_moves": [
    {"asset_id": "reachy/APPROVED_ASSET", "revision": "sha256:REPLACE_WITH_64_LOWERCASE_HEX_DIGITS"}
  ]
}
```

The config path must be absolute, the approved revision must match the staged
asset, and the gateway needs its own valid Reachy SDK deployment settings.
The MCP child receives no OpenRouter or Hermes credentials; the MCP SDK only
inherits its small system-safe environment set. No device is contacted until a
conversation starts and the configured restricted toolset initializes. This
is not a live hardware qualification.

With no integrations configured, Conversationalist has no extra tools
and simply responds as a cloud voice assistant.

## Optional reusable Puppeteer embodiment (fake-only)

A separate opt-in integration starts one persistent Puppeteer MCP stdio child for
one configured **fake** Reachy, StackChan or SO-ARM101 performer. It sends
approximate, content-free LiveKit idle/listening/speaking observations and lets
an authorized agent discover and explicitly request simulated emotions, dances
or gestures. No physical robot SDK, permission to move hardware or precise
speaker playback timing is implied. The existing restricted physical Reachy
move tool is separate and does not turn on automatically.

Configure `PUPPETEER_EMBODIMENT_CONFIG=/absolute/path/to/fake-performer.json`
(and optionally `PUPPETEER_EMBODIMENT_COMMAND`); leave unset for normal
robot-free startup. Follow [Embodiment reference integration](docs/EMBODIMENT.md)
for clean package install, all three fake profiles, MCP calls, action lookup,
software tests and physical qualification gaps.

## Optional native robot ensembles

The same LiveKit voice session can now embody **Reachy Mini and StackChan
together** without a simulated SO-ARM101. Puppeteer's merged
`schema_version: 3` deployment launches exactly those two native SDK
sessions under one `ConversationEnsemble`; Conversationalist validates
the exact two-performer catalog and starts only the existing room-owned
MCP child.

Use Puppeteer's review-only
`scripts/author_three_robot_ensemble.py --reachy-stackchan-only` generator,
approve real hardware identity, grants, Reachy neutral and StackChan USB
port, and set `PUPPETEER_ENSEMBLE_CONFIG` in the trusted worker. No
additional microphone, robot driver, artificial SO-ARM, or voice persona
is created. See [Native ensemble deployment](docs/NATIVE_ENSEMBLE.md).
This does not validate real hardware motion or install firmware.

## Optional native three-robot ensemble (PR #9)

A single authorized LiveKit voice room can drive **the same
idle/listening/speaking state** across Reachy Mini, StackChan and
SO-ARM101. This uses [Forge Puppeteer's merged native ensemble
runtime](https://github.com/AgenticForge-Labs/forge-puppeteer/pull/13),
not the stock Reachy Conversation App, direct motor controls, or a
second voice/LLM process. 4G remains a *conversation identity*, not a
new robot SDK. This integration does not automatically import the
stock 4G personality or business-tool configuration.

The mode is off by default. Put these settings in the **root worker**
`.env.local` only:

```bash
PUPPETEER_ENSEMBLE_CONFIG=/absolute/operator-reviewed/three-robots.json
# If installed outside the worker PATH:
PUPPETEER_ENSEMBLE_COMMAND=/absolute/path/to/forge-puppeteer-ensemble
```

Do not enable it alongside `PUPPETEER_MCP_CONFIG` or
`PUPPETEER_EMBODIMENT_CONFIG`. The child starts only when LiveKit's
trusted dispatch provides a stable authorized identity and the worker
has connected to that room. State callbacks are **approximate**
conversation state, not measured audio playout timing. The room keeps
its current OpenRouter/LiveKit speech models, voice, MCP and Hermes
tool configuration. The only extra agent tools are read-only
`get_ensemble_status` and explicitly requested, finite
`request_ensemble_gesture` (one explicitly selected performer and an exact catalogued finite behavior).

The SO-ARM must have exact operator-reviewed choreography pins and the
native SDK's independent supervisor/watchdog; simulation cannot
approve real-world motion. Startup of a three-robot SDK child can
contact physical devices, so inspect the config, isolate power, and
follow attended qualification before enabling it.

See [Native Ensemble](docs/NATIVE_ENSEMBLE.md) for version-pinned
installation, contracts, tests, and the physical acceptance checklist.

### Per-robot expression controls

When an operator-authorized native ensemble is configured, the active
conversation screen shows a **Robots and expressions** panel. Its buttons
come from Puppeteer's current `ensemble_state.performers[*].expressions`
catalog: no robot-specific gestures are manufactured by Conversationalist.
A connected authorized participant can send one named, one-performer
`forge.request_robot_expression` LiveKit RPC; `forge.get_robots`
is read-only. The worker verifies its signed participant identity and routes
the action through the existing serial Puppeteer MCP room and SDK safety.
Unavailable or blocked performers are shown but cannot be selected.
`accepted` means a request entered the scheduler, **not** that hardware
moved or settled. The UI does not retry ambiguous commands. Without an
authorized ensemble, voice works and this section reports that no robot
controls are available. See [Native Ensemble](docs/NATIVE_ENSEMBLE.md).

### Optional output-playback-driven robot presence

For an **attended, authorized** native Puppeteer ensemble, the worker supports
`CONVERSATIONALIST_PLAYBACK_PRESENCE=1`. It listens to the already-owned
LiveKit output's `playback_started` / `playback_finished` events, and starts
or stops the robot's `speaking` behavior using the existing Puppeteer room.
This is a deliberate improvement over reacting only to the model's
`agent_state_changed` state. Legacy presence remains the default.

**Important:** the default LiveKit output event is not measured arrival at
a remote browser speaker. It is a room-output publication event; amplitude,
phoneme timing, and acoustic synchronization remain future work. No PCM or
transcript is forwarded to the robot, and a missing output hook falls back to
the existing semantic state behavior. See
[Native Ensemble](docs/NATIVE_ENSEMBLE.md).

## Audio devices, expressions and notifications

The web UI lets each connected participant select a supported microphone
and speaker. LiveKit audio tracks—not Reachy or Puppeteer—own the
conversation media transport. Browser restrictions can prevent selecting
some speaker outputs. A future Reachy, room-audio, phone or other endpoint
can join the same room with its own supported audio adapter.

Microphone mode defaults to **Press to talk**. Hold the button to send audio;
releasing it, switching tabs, or leaving the window stops capture. The selected
mode is remembered in this browser only, so an existing **Continuous
listening** preference is restored before automatic reconnect. Only this mode
preference is stored in localStorage; access credentials use an HttpOnly cookie,
and the transcript is not stored in browser web storage.

The page also has **manual preview-only** emotion and notification controls,
even when no robot or LiveKit room is present. New mail is a soft ding,
new order a double ding, and an error a warning chime; no Platform event
feed or user mail/orders are read or simulated as actual records.
Cross-system event subscriptions, publisher/subscriber authorization, replay,
notification fanout and delivery policy belong to **AgenticForge Platform**.
Conversationalist only translates authorized events into cues. LiveKit room
data messages are not a general-purpose bus.
Preview chimes are optional/mutable. The semantic listening/speaking/idle
states, six small emotions and three notification kinds share one Python
`forge.presentation.v1` contract, documented in
[docs/PRESENTATION_SIGNALS.md](docs/PRESENTATION_SIGNALS.md).
All event content defaults to generic labels with **no automatic speech**.
Puppeteer may later render states/emotions via authorized native device
primitives; no audio amplitude wobble or robot driver is introduced here.

## Tests and safety

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
cd web && npm install && npm run check && npm run build
```

The app does not persist transcripts or audio to disk. AI provider and LiveKit
service retention depends on external settings. The development
`POST /api/session` token gate is NOT production authentication; do not
expose it publicly. No physical motion, purchasing or other consequential
capability should be enabled without upstream validation and approval.
Tests mock all external calls; real LiveKit, OpenRouter, Hermes and MCP
integration require separate attended local acceptance.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/PR_PLAN.md](docs/PR_PLAN.md), and [AGENTS.md](AGENTS.md).


### Provider-neutral speech envelope (software foundation)

The hardware-free analyzer in src/forge_conversationalist/speech_envelope.py
consumes **outgoing synthesized 16-bit PCM**, not microphone/STT data, and emits
only versioned forge.speech_envelope.v1 hop features: generated-audio offset,
50 ms duration, activity, smoothed intensity and RMS level. The design follows
the RMS/VAD/attack-release approach in the pinned Pollen Reachy Mini
speech_tapper.py, but not its robot-specific oscillators or GLib scheduler.
It does not record audio, text, or prompts.

**Status:** This is an analyzed-PCM primitive and offline tests, not a LiveKit
output tap, active Puppeteer transport, or physical feature. The dedicated
LiveKit TTS/output hook and authenticated, bounded Puppeteer ingress must be
wired, tested and released separately. A generated_pcm offset is not a LiveKit
output-track timestamp, local speaker acoustic time, or SDK motion completion.
Default behavior is unchanged and no new robot motion is authorized.


### Opt-in outgoing speech envelope preview

With a previously authorized Puppeteer ensemble and its matching optional
MCP tool, set CONVERSATIONALIST_OUTGOING_SPEECH_ENVELOPE=1 in the trusted worker
environment. Conversationalist observes **only outgoing synthesized TTS PCM**
through LiveKit's standard Agent.default.tts_node, passes the exact same
AudioFrame objects onward, and batches content-free 50 ms loudness/voice
activity hops to the **existing** room-owned Puppeteer MCP child. A missing
tool, unsupported audio format, queue pressure, or MCP failure never starts a
new motor owner and does not stop the normal audio response.

The feature contract is forge.speech_envelope.v1. Its timing_source is
generated_pcm and acoustic_playout_measured is always false. The Puppeteer
receiver only offers a read-only preview and device-neutral shaping; no
physical motion is enabled by this setting. This feature does not see raw
microphone audio or forward any PCM, text, transcription, model credentials
or speaker device details to Puppeteer.

Actual EMEET speaker playback delay and interruption timing are unmeasured.
The existing opt-in playback presence path can operate alongside this
feature; it does not make generated PCM offsets acoustically synchronized.
Real audio-reactive robot rendering remains a separate physically qualified
and operator-granted task. The stock Pollen Conversation App must stay stopped
while Puppeteer owns Reachy.
