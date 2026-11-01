# Forge Conversationalist architecture

## Current scope — expanded PR 1 (unmerged)
A device-independent LiveKit voice worker and browser client supporting three
cloud profiles: LiveKit Inference, OpenRouter, and hybrid stage selection.
A default room has one human participant and one conversational agent.
No robot, Hermes session, MCP server, company data, local LLM, or persistent
memory is required.

Browser audio -> LiveKit WebRTC room -> LiveKit Agents voice session
  -> independently selected STT, LLM, TTS models
  -> LiveKit room -> browser speakers.

LiveKit owns VAD, turn detection, realtime media, playback and interruption.
For OpenRouter STT we explicitly select the *non-realtime/buffered* OpenAI
plugin adapter. OpenRouter has dedicated HTTP transcription and speech
endpoints, not a guaranteed WS STT connection. That imposes different
response latency from LiveKit's streaming speech options. OpenRouter TTS is
chunked via the existing LiveKit OpenAI-compatible plugin. Genuine service
compatibility, formats, voices and latency require attended provider testing;
mock tests are not evidence of actual playout.

Only the worker process receives OPENROUTER_API_KEY. The URL is pinned to
https://openrouter.ai/api/v1 in trusted source, not passed from a browser.
No duplicate model client, audio transport, agent memory or voice server.
Silero VAD can run on CPU; no local LLM is required.

## Provider profiles
- livekit: LiveKit STT / LLM / TTS, unchanged baseline.
- openrouter: OpenRouter Parakeet STT, selected LLM, Qwen TTS.
- hybrid: LiveKit STT / OpenRouter LLM / LiveKit TTS by default.
- Each stage can be overridden independently using explicit environment
  fields. Provider/model/voice selections are validated before startup.
- Model catalogs and supported voices change; deployment should check current
  OpenRouter model metadata. Qwen Flash's loongjohn voice is not Reachy's
  Aiden voice and must not be described as a voice clone or equivalent.
- No implicit provider fallback or credential routing: failures are errors,
  not permission expansions or hidden additional billable requests.

## Contract boundaries
- The browser sends `lk.chat` text to the LiveKit room for typed input.
- LiveKit publishes speech/text output using its native transcript streams.
- The only web API exposed here is `POST /api/session`, a guarded room
  token issuance endpoint creating a random room and explicitly dispatching
  one named agent. No arbitrary user-selected room or agent names.
- The development access token is not production authentication: any
  production deployment needs actual identity, rate limiting and review.
- No audio or transcript is persisted by this app; retention in selected
  remote AI providers depends on their own service terms and settings.
- Stage selection comes only from trusted worker configuration.
- No live cloud or hardware validation has been performed on this branch.

## Future additive integrations — not yet implemented
Next: authenticated generic MCP and Platform gateway connections.
Then: external Hermes session adapters, multi-device/agent routing, optional
Puppeteer performance based on real playback events.

These future adapters cannot bypass their owning services or assume
unmerged sibling PRs are present in main.
