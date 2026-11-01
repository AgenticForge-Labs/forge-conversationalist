# Forge Conversationalist

A device-independent conversational interface for AgenticForge Labs. The project starts with a LiveKit-backed, cloud-inference voice experience; optional external agent sessions, MCP gateways, and physical embodiment are separate integrations.

## Repository status

The initial `main` commit establishes the repository only. All executable functionality belongs in reviewed feature PRs. See the first LiveKit foundation PR for the application, tests, setup, and run instructions.

## Ownership

LiveKit owns realtime transport, voice sessions, and audio turn management. This repository owns configuration, conversational interface and adapter boundaries. Hermes owns its agent sessions, AgenticForge Platform owns operational state and evidence, Forge Puppeteer owns physical performance, and device SDKs own hardware execution and safety.

No connected robot, local LLM, or MCP gateway is required for standalone voice operation.
