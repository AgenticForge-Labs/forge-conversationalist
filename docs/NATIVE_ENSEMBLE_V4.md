# Native ensemble schema v4

Puppeteer schema v4 supports any nonempty selection of Reachy, StackChan, and
SO-ARM101. The room file lists `{kind, id, file}` references; relative paths
resolve beside that room file. Every referenced robot file repeats `kind` and
`id` and contains its existing provider configuration under `provider`.

Conversationalist validates unique kinds and IDs, exact file identity, native
provider/backend identity, selected performer IDs, and absolute shared journal
paths before launching the room. It hashes the referenced bytes so edits after
preflight invalidate the operator config. The same Puppeteer MCP room and SDK
authority rules apply. Only schema 4 is supported; convert older files with Puppeteer's
`scripts/migrate-room-config.py`. See Puppeteer's
`docs/ENSEMBLE_V4.md` for the room example.

The room file carries `"physical": true` (real robots) or `false` (simulated
SO-ARM only) instead of the old `motion_authority` marker, and the per-robot
`physical_authority` marker is gone. Reachy and StackChan robot files may carry an
`options` object; Conversationalist reads it only to learn which performers are
speech-driven and which Reachy profiles (`idle: original_v1`, `speaking: smooth_v2`)
are selected, so it sends speech features and native speech only where Puppeteer
will use them. An SO-ARM robot file may set `sleep_target` and `sleep_check`.
At startup the Puppeteer room checks each real robot against its sleep pose and
holds still any that is not there; its `ensemble_startup_check`, `ensemble_prepare`
and `ensemble_park` tools exist for that, and a session should end with
`ensemble_park`.
