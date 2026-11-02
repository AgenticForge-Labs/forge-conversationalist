import assert from "node:assert/strict";
import { test } from "node:test";
import { combineTranscript, parseTranscriptResponse, shouldRestoreTranscript } from "./transcript.mjs";

test("RPC transcript parsing validates, caps, and namespaces stable history IDs", () => {
  const parsed = parseTranscriptResponse(JSON.stringify({
    messages: [
      { id: "one", role: "user", text: "Hello" },
      { id: "one", role: "user", text: "Duplicate history ID" },
      { id: "bad", role: "robot", text: "Ignore" },
      { id: "two", role: "assistant", text: "Reply" },
    ],
  }));
  assert.deepEqual(parsed, [
    { id: "history:one", role: "user", text: "Hello" },
    { id: "history:two", role: "assistant", text: "Reply" },
  ]);
  assert.deepEqual(parseTranscriptResponse("not-json"), []);
  assert.deepEqual(parseTranscriptResponse({ messages: "invalid" }), []);
});

test("history and live streams merge by namespaced stable IDs and keep repeated text", () => {
  const rows = combineTranscript(
    [{ id: "history:1", role: "user", text: "Again" }],
    [
      { streamInfo: { id: "1" }, participantInfo: { identity: "me" }, text: "Again" },
      { streamInfo: { id: "2" }, participantInfo: { identity: "agent-x" }, text: "Answer" },
      { streamInfo: { id: "2" }, participantInfo: { identity: "agent-x" }, text: "Updated answer" },
    ],
    "me",
  );
  assert.deepEqual(rows, [
    { id: "history:1", role: "user", text: "Again" },
    { id: "live:1", role: "user", text: "Again" },
    { id: "live:2", role: "assistant", text: "Updated answer" },
  ]);
});

test("initial history restore runs only for resumed rooms; RTC reconnect always restores", () => {
  assert.equal(shouldRestoreTranscript("connected", false), false);
  assert.equal(shouldRestoreTranscript("connected", true), true);
  assert.equal(shouldRestoreTranscript("reconnected", false), true);
  assert.equal(shouldRestoreTranscript("reconnected", true), true);
});
