# Forge Conversationalist architecture

## Scope of PR 1
A device-independent, cloud-inference voice worker and browser client. A default
room contains one human participant and one voice agent. No robot, Hermes session,
MCP server, private company data, local LLM, or persistent user memory is required.

Browser audio -> LiveKit room -> LiveKit Agents STT / LLM / TTS (Cloud Inference)
-> LiveKit room audio -> browser speakers.

The agent worker owns only the conversation instructions and selected model IDs.
LiveKit owns VAD, turn detection, interruption, streaming media, and its agent
session lifecycle. Silero VAD is CPU-local and does not host a language model.
The browser does not invoke model APIs directly and never receives server secrets.

## Contract boundaries
- The browser sends `lk.chat` text to the LiveKit participant when typed text is used.
- LiveKit publishes speech/text output using its native transcript streams.
- The only web API exposed by the app is `POST /api/session`, a guarded room
  token issuance endpoint that creates a random one-user room and requests one
  named LiveKit agent dispatch; no unrestricted room selection.
- A local-development access passphrase protects token issuance and is not a
  production identity provider; production deployment requires real authentication,
  rate limits, logging policies and live threat review.
- No raw microphone audio is retained by this repo. Browser transcript view is
  ephemeral and resets with the page; LiveKit/provider handling is subject to
  their configured service policy.
- STT, LLM, TTS are selected independently through validated model identifiers.

## Future additive integrations — not yet implemented
PR 2: OpenRouter audio/LLM provider parity.
PR 3: authenticated generic MCP and Platform gateway connections.
PR 4: external Hermes agent-session adapter (no duplicate agent state).
PR 5: participant/mic/speaker routing policy.
PR 6: optional Puppeteer performance through playback events.

These future adapters must never bypass their owning systems or assume
unmerged work in sibling repositories.
