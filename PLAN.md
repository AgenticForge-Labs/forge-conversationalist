# Development plan

This document summarizes acceptance priorities; it does not grant access or assert production readiness. See [status and development history](STATUS.md), [architecture](docs/ARCHITECTURE.md), and the detailed [PR plan](docs/PR_PLAN.md).

- Preserve device-independent browser and local-audio voice operation and keep optional MCP, Hermes and robot integrations explicitly configured.
- Complete attended no-robot integration acceptance: identity, scoped tools, actual task-result polling, privacy, interruption and room recovery.
- Qualify any authorized Puppeteer/robot ensemble separately, including physical clearance, neutral and stop handling, interruption and ambiguous outcomes.
- Validate opt-in notification and speech-envelope integrations with their owning services; preview data and generated-audio timing are not evidence of physical playback or robot motion.
- Keep LiveKit responsible for realtime audio, Hermes responsible for its runs, Platform responsible for business authority, and Puppeteer/SDKs responsible for robot execution.
