# Mostly linear PR program

PR 0 (main): repository README initialization.
PR 1 (OPEN draft, unmerged): standalone LiveKit Cloud/Inferences + OpenRouter
and hybrid speech pipeline, browser client, gated room tokens, offline tests.
PR 2 (this stacked OPEN draft, based on PR 1): optional narrow MCP toolset
plus Hermes Runs API delegation and status tools, tests, source-controlled
architecture/security examples, credential and failure handling.
PR 3 (after merge of PRs 1 and 2): Hermes-primary API-owned sessions and,
separately, authenticated gateway WebSocket integration for existing
CLI/TUI-owned sessions if needed; progress subscriptions and quiet-break
completion notification.
PR 4: participant/mic/speaker/agent routing policy.
PR 5: Puppeteer integration only after its required dependency PRs merge
and attended hardware safety validation is performed.

Strict dependency order: PR 1 -> PR 2 -> later changes.
Do not merge PR 2 while PR 1 remains unmerged. Avoid a new branch from
main until dependent work is integrated.
