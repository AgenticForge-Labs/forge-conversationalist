import test from "node:test";
import assert from "node:assert/strict";
import { decodeSessionBinding, encodeSessionBinding } from "./session-binding.mjs";

const configured = "test access token material that is at least thirty two bytes";
const binding = {
  roomName: "conversationalist-123e4567-e89b-12d3-a456-426614174000",
  identity: "person-123e4567-e89b-12d3-a456-426614174001",
};

test("signed session binding round trips", () => {
  const cookie = encodeSessionBinding(binding, configured);
  assert.deepEqual(decodeSessionBinding(cookie, configured), binding);
});

test("session binding rejects a changed signature or signing key", () => {
  const cookie = encodeSessionBinding(binding, configured);
  const [payload, signature] = cookie.split(".");
  const changedSignature = `${payload}.${signature[0] === "a" ? "b" : "a"}${signature.slice(1)}`;
  assert.equal(decodeSessionBinding(changedSignature, configured), undefined);
  assert.equal(decodeSessionBinding(cookie, `${configured}!`), undefined);
});

test("session binding rejects correctly signed arbitrary room and identity values", () => {
  const cookie = encodeSessionBinding({ roomName: "other-room", identity: "person-other" }, configured);
  assert.equal(decodeSessionBinding(cookie, configured), undefined);
});
