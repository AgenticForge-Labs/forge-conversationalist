# Mostly linear PR program

PR 0 (main): minimal README to initialize an otherwise empty repository.
PR 1 (expanded, OPEN DRAFT): LiveKit room, browser UI, cloud voice worker,
    Silero VAD, native turn handling, guarded tokens, plus independent
    OpenRouter / LiveKit Inference STT, LLM, TTS provider selection and
    hybrid profile. Both providers need attended live audio qualification.
    OpenRouter scope was incorporated into the existing unmerged PR by
    direct owner request instead of building a dependent branch from main.
After PR 1 is qualified and merged:
PR 3: standard authenticated MCP client and optional ContextForge virtual server.
PR 4: Hermes/external agent session adapter.
PR 5: multi-participant microphone, speaker and agent routing.
PR 6: optional Puppeteer integration once required sibling contracts merge.

Keep validation source-controlled and incorporate discovered source fixes in
the same PR. Do not claim mock tests prove remote provider compatibility.
