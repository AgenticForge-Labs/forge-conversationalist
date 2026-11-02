import assert from "node:assert/strict";
import { test } from "node:test";
import { canUseRoomInput, describeSessionStartFailure, endBoundSession, shouldEnableContinuousMicrophone, STALE_AGENT_SESSION_MESSAGE } from "./session-actions.mjs";

test("explicit end deletes the bound session with same-origin credentials", async () => {
  let request;
  await endBoundSession(async (url, options) => {
    request = { url, options };
    return { ok: true };
  });

  assert.equal(request.url, "/api/session");
  assert.equal(request.options.method, "DELETE");
  assert.equal(request.options.credentials, "same-origin");
  assert.equal(request.options.cache, "no-store");
});

test("explicit end reports API and network errors to the caller", async () => {
  await assert.rejects(endBoundSession(async () => ({ ok: false })), /Could not end/);
  await assert.rejects(endBoundSession(async () => { throw new Error("offline"); }), /offline/);
});

test("stale agent errors surface the recovery action without treating other failures as stale", () => {
  assert.deepEqual(describeSessionStartFailure(503, { error: STALE_AGENT_SESSION_MESSAGE }), {
    message: STALE_AGENT_SESSION_MESSAGE,
    requiresReset: true,
  });
  assert.equal(describeSessionStartFailure(503, { error: "Unable to check the existing conversation" }).requiresReset, false);
  assert.equal(describeSessionStartFailure(401, {}).message, "Invalid local development access token.");
});

test("room input waits for a resumed or reconnecting session handshake", () => {
  assert.equal(canUseRoomInput(false, false), true);
  assert.equal(canUseRoomInput(true, false), false);
  assert.equal(canUseRoomInput(true, true), true);
});

test("continuous microphone stays muted until resumed readiness completes", () => {
  assert.equal(shouldEnableContinuousMicrophone("continuous", false), false);
  assert.equal(shouldEnableContinuousMicrophone("continuous", true), true);
  assert.equal(shouldEnableContinuousMicrophone("press-to-talk", true), false);
});
