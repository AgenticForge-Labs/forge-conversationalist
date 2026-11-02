# Device-independent presentation signals v1

## Purpose
Conversationalist can be useful on any audio-capable endpoint, with or
without a robot. **Audio transport is separate from expression**. LiveKit
participants use the microphone/speaker available to their own operating
system, browser, SIP client or future SDK adapter. Reachy, StackChan, status
lamps and displays are optional presentation surfaces, not conversation hosts.

The canonical pure mapping is `forge_conversationalist.presentation`, not an
agent prompt, robot animation or Platform notification handler. This version
is small on purpose. The same semantic cue can be rendered as a visual
status, a short chime, an approved light pattern or a Puppeteer behavior.
Renderers may safely skip capabilities they lack.

## Signal vocabulary
- `conversation/idle`, `conversation/listening`,
  `conversation/speaking`: declarative current presence. These are *not*
  speech-amplitude-driven motions and do not infer emotional content.
- `emotion/neutral`, `happy`, `curious`, `confused`,
  `concerned`, `excited`: small optional emotion vocabulary. Puppeteer
  (not Conversationalist) decides how, whether and when an authorized
  robot expresses it. Static lights/icons are equally valid.
- `notification/mail`, `notification/order`, `notification/failure`:
  **generic indicator classes**. The canonical source of truth for mail,
  commerce, and workflow failure remains the owning provider/Platform.
  They map respectively to `soft_ding`, `double_ding`, and `warning`
  sound intents plus bounded, generic visual labels.

## Wire contract
`forge.presentation.v1` on the optional LiveKit data-packet topic
`forge.presentation.v1`. Typed server-side `PresentationEvent` contains
`event_id`, `category`, `value`, `privacy`. A deterministic
`plan_cue` emits:

```json
{
  "schema": "forge.presentation.v1",
  "event_id": "order-evt-123",
  "category": "notification",
  "value": "order",
  "visual_label": "New order",
  "sound": "double_ding",
  "emotion": null,
  "privacy": "private",
  "auto_speak": false
}
```

All notification cues have `auto_speak: false`. No email body, customer
identity, order data, failed command output or full record is copied into
the cue. A separate, authenticated user action would be required to read
private content aloud. Chimes are short and user-mutable; they do not
replace an audit log or imply that a business process succeeded.

`PresentationRouter` suppresses duplicate IDs with a *bounded
ephemeral* cache, not a durable delivery guarantee. Future Platform-owned subscription delivery needs authenticated sources,
subscriber grants, replay/delivery policy and user/device preferences.
The owning source system still retains business and hardware truth. A LiveKit room
topic is **not an authenticated subscription bus**. Browser clients must
accept official cues only from a verified/authorized publisher; a user
posting the same JSON to a room must not be treated as a new company fact.
The generic `publish_cue` utility is not called by the voice worker
automatically; no source feed is wired.

## Present renderers and deferred work
**Present in this PR:** typed Python plans, pure tests, optional LiveKit
packet publisher, microphone/speaker selection for browsers that support
those devices, listening/speaking/idle readout, and **manual, clearly
labeled browser-only previews** for emotions and three chimes. Preview
events are not actual orders/mail/failures and are not transmitted to the
gateway or agent. A browser may lack permission to switch speaker output.

**Future:** a trusted **Platform communications bus** with subscriber authorization,
event filtering, privacy and multi-device delivery, verified signal ingress into
the LiveKit room, actual device-specific light/display/Puppeteer renderers,
and robust selected-speaker arbitration for shared microphones.

When integrating Puppeteer, consume this semantic state and map it through
Puppeteer's existing approved `ConversationBehaviors` / native SDK
behaviors. No bespoke head wobble, arbitrary motion or direct SDK command
belongs in this repository. The merged Puppeteer PR #13 provides the three-performer native SDK
semantic room in software; Conversationalist PR #9 adds one LiveKit
state-to-MCP bridge. Live hardware safety and speaker-timing validation
remain deferred. No current physical hardware validation
is implied.
