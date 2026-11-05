import { test } from "node:test";
import assert from "node:assert/strict";
import { allowsLocalAutoLogin, isLoopbackDevelopment } from "./local-auto-login.mjs";

test("local automatic sign-in is opt-in, development-only and same-origin", () => {
  const local = { enabled: "true", nodeEnv: "development", hostname: "localhost", fetchSite: "same-origin" };
  assert.equal(allowsLocalAutoLogin(local), true);
  assert.equal(allowsLocalAutoLogin({ ...local, hostname: "127.0.0.1" }), true);
  for (const change of [{ enabled: "false" }, { nodeEnv: "production" },
    { hostname: "example.com" }, { fetchSite: "cross-site" }, { fetchSite: null }]) {
    assert.equal(allowsLocalAutoLogin({ ...local, ...change }), false);
  }
});

test("non-browser loopback clients in development need no cookie or fetch metadata", () => {
  const browser = { enabled: "true", nodeEnv: "development", hostname: "localhost", fetchSite: "same-origin" };
  // The browserless audio participant and scripted clients send no Sec-Fetch-Site.
  assert.equal(allowsLocalAutoLogin({ ...browser, fetchSite: null }), false);
  assert.equal(allowsLocalAutoLogin({ ...browser, fetchSite: null, allowAnyLoopback: true }), true);
  for (const change of [{ nodeEnv: "production" }, { hostname: "example.com" }, { hostname: "192.168.1.10" }]) {
    assert.equal(allowsLocalAutoLogin({ ...browser, allowAnyLoopback: true, ...change }), false);
  }
});

test("isLoopbackDevelopment is development-only and loopback-only", () => {
  assert.equal(isLoopbackDevelopment({ nodeEnv: "development", hostname: "localhost" }), true);
  assert.equal(isLoopbackDevelopment({ nodeEnv: "development", hostname: "127.0.0.1" }), true);
  assert.equal(isLoopbackDevelopment({ nodeEnv: "production", hostname: "localhost" }), false);
  assert.equal(isLoopbackDevelopment({ nodeEnv: "development", hostname: "10.0.0.5" }), false);
});
