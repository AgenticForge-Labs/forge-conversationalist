# Repository development rules

## November 2026 company interface acceptance

Treat Conversationalist/4G as an optional owner-facing LiveKit interface,
**not** the six-role Business Engine's Executive, Platform Work Coordinator,
a privileged MCP server, or Hermes' session authority. The Business Engine
chooses company semantic work; the Platform authenticates/adopts WorkItems,
enforces permissions and approvals, and retains durable evidence.

For local end-to-end acceptance, begin with no robot: verify authenticated
owner intent, exact server-side tool grants, a bounded Business Engine
request or read, completion-aware result polling, sourced status, interruption
without automatic retry, and no secret/private-content disclosure. Never
assume that voice interruption cancels an already admitted consequential
action. Physical startup, neutral/sleep, STOP and connection uncertainty
remain owned by Puppeteer/SDKs and require attended validation.

See [company control](https://github.com/AgenticForge-Labs/agenticforge-platform/blob/main/docs/company-control-and-delegation.md)
and [activation roadmap](https://github.com/AgenticForge-Labs/agenticforge-platform/blob/main/docs/company-activation-roadmap-2026-11.md).
No production action is authorized by these documentation changes.

Read organization-level `development.md` and `architecture.md` first.
GitHub main is the authoritative integrated state; inspect open PRs.
PRs #1–#3 are merged into main. Puppeteer native three-robot ensemble PR #13 is merged; this repository's PR #9 connects an authorized LiveKit room to its fixed semantic MCP interface.

## Boundaries
- LiveKit owns realtime audio, rooms, VAD and playback.
- Conversationalist owns its lightweight agent configuration and optional tools.
- Use native LiveKit MCPToolset for MCP, with exact server-side authorized grants;
  do not create a second gateway or trust client-side filtering as security.
- Hermes owns its API session, toolset, runs, memory and longer work. Delegate
  using its authenticated Runs API; do not clone its agent/session authority.
- Platform owns operational evidence and ContextForge tool federation.
- Puppeteer owns semantic robot performance; SDKs own hardware execution and safety.

## Safety and tests
- All MCP/Hermes credentials stay in Python worker env, not web/.env.local.
- MCP must have a nonempty explicit tool allowlist and scoped credential; no
  server-provided wildcard tools by default.
- Consequential writes and robot motion require upstream owner approvals and
  hardware safety. This repo exposes no raw robot driver.
- Hermes submissions return admission only; poll actual status to claim a
  completed task. Do not automatically retry ambiguous submissions.
- No Hermes TUI/CLI session takeover via API. No automatic completion push.
- Cloud/provider authentication, microphone playout, MCP, Hermes and physical
  operations need attended acceptance; unit-test green is not production ready.
- Keep PR statuses and exact SHAs in handoff.

## Reusable fake-only embodiment v1
- Puppeteer PR #10 merged `forge.embodiment.v1` to main. The optional
  `PUPPETEER_EMBODIMENT_CONFIG` path is a strictly fake-only MCP child;
  avoid duplicating robot SDKs and never interpret simulated expressions as
  physical qualification.
- Use `SessionStateHook` for approximate `agent_state` presence, with
  versioned sequence and periodic state refresh. Do not call these measured
  output start/drain times. Never automatically retry unknown action outcomes.
- One room-owned MCP child handles both observation and explicit expression.
  Do not start a parallel process for the same room's expressions.
- Preserve no-robot startup and the separate restricted Reachy MCP path.

## Device-independent presence and notifications
- Audio belongs to a LiveKit participant, not to a particular robot or
  personality. Browser audio selectors can change only supported devices.
- Canonical presentation schema is `forge.presentation.v1` in
  `presentation.py`: idle, listening, speaking; neutral, happy, curious,
  confused, concerned, excited; mail, order, failure generic indicators.
- Platform (or owning producer) retains mail/order/error truth and replay
  authority; no event subscription is implemented by this repo.
- Renderers may use icons, light, sound or approved Puppeteer semantics.
  No microphone-loudness-driven motion, raw motor commands or silent
  widening of robot permissions. No subscriber should trust an unverified
  LiveKit room packet as evidence of an actual company event.
- Never automatically speak private notification content; browser
  notification examples must be clearly labeled manual previews.
- Optional `CONVERSATIONALIST_COMPANY_NOTIFICATIONS=1` extends the same
  verified tenant-scoped development topic to exact, generic order and
  printer/work failure cues. It does not speak finance or GitHub activity,
  invent order confirmations, or authorize robot motion. It uses existing
  `mail/order/failure` renderer values only; the mail-only feature remains
  backward compatible.
- Optional `CONVERSATIONALIST_MAIL_STREAMS=1` is a **development-only**
  server-side Hatchet subscriber for source-linked `mail.received` Platform
  notices. Only signed rooms with a pinned business and existing mail-read
  tools may start it. It speaks a constant nonprivate question and delivers a
  private generic UI indicator; never sender/subject/body and no automatic
  email reading. The worker holds isolated development Hatchet credentials,
  not the browser/LLM/robot. This is an explicitly restricted trial, not a
  production subscriber authorization replacement. Do not change Puppeteer
  motion, safety or room-presence ownership to implement notifications.


## Optional two-robot Reachy + StackChan deployment

- Puppeteer config `schema_version: 3` explicitly selects exactly Reachy
  and StackChan. Preserve older v1 three-performer and v2 Reachy-only
  configurations. The worker must reject a v3 SO-ARM record and a child
  that announces an unexpected/missing performer.
- Reuse one `EnsembleSession` and its current stdio MCP child for all
  speaking, listening, gestures and optional envelope preview. Never
  launch two SDK owners against Reachy or StackChan.
- Do not accept hardware authority from the presence of a config file:
  signed room identity, operator grants, live SDK/firmware capabilities,
  Reachy's physical neutral and uncertain STOP quarantine still govern.
- A StackChan EXT CONTROL icon disappearing can be caused by AI Agent
  mode or firmware OTA; diagnose with the SDK's read-only
  `docs/disappearing-app-recovery.md` guide. Conversationalist must not
  automatically flash, erase, reconnect, retry or rearm robots.

## Native robot expression controls (2026-11-02)
- Only Puppeteer owns the authoritative, approved finite expression catalog;
  fetch `ensemble_state.performers[*].expressions` from the same room-owned
  MCP child, never guess which gestures a robot has.
- UI and agent tools must use explicit `performer_id` and a currently
  catalogued name. Automatic speaking/listening/idle are NOT manual gestures.
- Both robot RPC methods are registered only after authorized ensemble
  startup and are guarded by the signed LiveKit participant identity.
  Never move motion policy or hardware permission to the browser.
- Admission is not physical completion; preserve no retries and honest
  unknown outcomes. Availability and quarantine remain Puppeteer/SDK-owned.
- Do not silently import every upstream recording: an immutable reviewed
  behavior pack is the sole selection boundary.


## Experimental LiveKit output-presence seam (2026-11)

- `CONVERSATIONALIST_PLAYBACK_PRESENCE=1` is opt-in for a previously
  authorized native ensemble. Default LiveKit room output events describe
  audio sent to a track, NOT actual remote speaker/acoustic playout.
- Bind `session.output.audio` after `session.start`; never create a new
  audio sink, duplicate microphone/speaker service, or start a second
  Puppeteer runtime just to observe playback events.
- Semantic agent speaking is not itself evidence that audio has begun;
  `playback_started` gates the opt-in speaking motion state.
- User interruption remains urgent, pending output callbacks cannot silently
  rearm movement, and timeout/unconfirmed STOP semantics remain unchanged.
- Preserve source attribution: `agent_state` vs. `playback`; neither
  promises PCM/phoneme accuracy or physical motion completion.
- A missing/incompatible output hook falls back to existing semantic
  presence. Keep tests with fake output emitters and fake robots; physical
  acceptance and precise playback calibration are independent gates.


## Provider-neutral outgoing speech envelope

- A hardware-free forge.speech_envelope.v1 analyzer uses only outbound
  synthesized PCM. It does not inspect microphone samples, words, transcripts,
  or company data.
- The synthesized-audio time axis is generated_pcm, **not** actual LiveKit
  track playout, remote/browser speaker output, or physical motion.
- Own the real LiveKit pipeline tap in Conversationalist. Do not construct
  a second sink, decoder, player, or audio service for Puppeteer.
- Preserve the same room-owned MCP client, ordered presence, immediate
  interruption and uncertain STOP quarantine. Never pass raw PCM to robots.
- The analyzer is opt-in on the existing room-owned TTS stream and feature
  queue; only Puppeteer's independently reviewed motion profile may
  turn fresh features into SDK-targeted speaking. These are not acoustically
  synchronized to a speaker; each activation stage must be explicit.


## LiveKit outgoing PCM observation (opt-in; SDK dispatch only when separately granted)

- CONVERSATIONALIST_OUTGOING_SPEECH_ENVELOPE=1 wraps only outgoing synthesized
  TTS frames with the default LiveKit Agent.tts_node stream; original audio
  frames still flow directly to LiveKit. No second sink, decoder, media engine,
  microphone capture or speaker controller is created.
- The existing authorized EnsembleSession negotiates an optional
  ensemble_speech_envelope tool and offers at most eight content-free hops
  per MCP batch without blocking voice or saturating important presence events.
  Queue pressure disables that stream's observation; it is never retried.
- Frame offsets are generated PCM time, not measured actual speaker playout.
  The ingestion tool only updates observations. The Puppeteer PR #40
  `smooth_v2` profile can drive motion through the *same* SDK runtime if and
  only if independently authorized; there is no new motion grant from audio.
- Preserve existing unknown-action quarantine. A late PCM batch must not
  rearm or move a performer. An urgent interrupted state must overtake queued
  optional batches, and old streams remain fenced.

## PR2 room lifecycle and nonmoving preflight (2026-11-05)
- Puppeteer PR #40 is merged at
  `979da719834b1e04641097555eb017c40d55f011`; use this SHA,
  not historical PR #13/25 pins. Conversationalist PR #31 owns the
  dependent software integration on one branch, not a new robot controller.
- `PuppeteerEnsembleConfig.preflight()` and the preflight CLI validate
  local operator config, exact identities, independent profile choices,
  reviewed neutral file and grants without opening any SDK or MCP connection.
  An offline pass is never evidence of physical permission or safety.
- Startup must verify the authoritative Puppeteer behavior catalog,
  SHA-pinned revision formatting, exact performer IDs and live SDK readiness
  before voice tools or room presence. Failure falls back to no-robot voice.
- The room-owned child closes on worker shutdown, signed owner departure or
  LiveKit room disconnect. It reports bounded, per-performer disconnect
  evidence. Do not claim physically settled, neutral or asleep based on SDK
  acknowledgement, daemon health or LiveKit callback.
- Do **not** issue an automatic neutral or sleep after ambiguous STOP; those
  remain Puppeteer/hardware-gated (separate PR #20). Quarantine and
  operator reconciliation prevent auto-reconnect/rearm.
- `forge.set_robot_connection` must not report an unconfirmed shutdown as
  successful. Preserve exact signed participant identity checks.
