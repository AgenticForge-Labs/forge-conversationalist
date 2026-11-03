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
one of `yes`, `no`, `acknowledge`, `neutral`. Ordinary
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
