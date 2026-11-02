import { test } from "node:test";
import assert from "node:assert/strict";
import { allowsLocalAutoLogin } from "./local-auto-login.mjs";

test("local automatic sign-in is opt-in, development-only and same-origin", () => {
  const local = { enabled: "true", nodeEnv: "development", hostname: "localhost", fetchSite: "same-origin" };
  assert.equal(allowsLocalAutoLogin(local), true);
  assert.equal(allowsLocalAutoLogin({ ...local, hostname: "127.0.0.1" }), true);
  for (const change of [{ enabled: "false" }, { nodeEnv: "production" },
    { hostname: "example.com" }, { fetchSite: "cross-site" }, { fetchSite: null }]) {
    assert.equal(allowsLocalAutoLogin({ ...local, ...change }), false);
  }
});
