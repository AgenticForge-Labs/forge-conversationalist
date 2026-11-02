import assert from "node:assert/strict";
import { test } from "node:test";
import { PressToTalkController, pttCaptureOptions } from "./press-to-talk.js";
import { MICROPHONE_MODE_KEY, parseMicrophoneMode } from "./microphone-mode.mjs";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function makeTrack() {
  return { stopped: 0, stop() { this.stopped += 1; } };
}

test("release before microphone permission resolves never publishes audio", async () => {
  const capture = deferred();
  const candidate = makeTrack();
  let publishes = 0;
  const controller = new PressToTalkController(() => capture.promise, async () => { publishes += 1; }, async () => {});

  const pressing = controller.press();
  await controller.release();
  capture.resolve(candidate);
  await pressing;

  assert.equal(publishes, 0);
  assert.equal(candidate.stopped, 1);
});

test("release while publishing stops and unpublishes the in-flight track", async () => {
  const publication = deferred();
  const candidate = makeTrack();
  let unpublishes = 0;
  const controller = new PressToTalkController(async () => candidate, () => publication.promise, async () => { unpublishes += 1; });

  const pressing = controller.press();
  await Promise.resolve();
  const releasing = controller.release();
  publication.resolve();
  await Promise.all([pressing, releasing]);

  assert.equal(candidate.stopped, 1);
  assert.equal(unpublishes, 2);
});

test("press-to-talk uses the selected microphone and leaves the default unconstrained", () => {
  assert.deepEqual(pttCaptureOptions({ getActiveDevice: () => "usb-mic" }), { deviceId: "usb-mic" });
  assert.equal(pttCaptureOptions({ getActiveDevice: () => undefined }), undefined);
});

test("press-to-talk preference restores safely before reconnect and capture waits for a hold", async () => {
  assert.equal(MICROPHONE_MODE_KEY, "forge_conversationalist_microphone_mode");
  assert.equal(parseMicrophoneMode("press-to-talk"), "press-to-talk");
  assert.equal(parseMicrophoneMode("continuous"), "continuous");
  assert.equal(parseMicrophoneMode("unknown"), "press-to-talk");
  assert.equal(parseMicrophoneMode(null), "press-to-talk");

  const candidate = makeTrack();
  let captures = 0;
  let publishes = 0;
  const controller = new PressToTalkController(async () => { captures += 1; return candidate; }, async () => { publishes += 1; }, async () => {});
  assert.equal(captures, 0);
  assert.equal(publishes, 0);
  await controller.press();
  assert.equal(publishes, 1);
  await controller.release();
});
