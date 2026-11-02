import { createHmac, timingSafeEqual } from "node:crypto";

function signature(payload, configured) {
  return createHmac("sha256", configured).update(`forge-conversationalist-session-v1:${payload}`).digest();
}

export function encodeSessionBinding(binding, configured) {
  const payload = Buffer.from(JSON.stringify(binding)).toString("base64url");
  const mac = signature(payload, configured).toString("base64url");
  return `${payload}.${mac}`;
}

export function decodeSessionBinding(value, configured) {
  const [payload, mac, ...extra] = value.split(".");
  if (!payload || !mac || extra.length) return undefined;
  const received = Buffer.from(mac, "base64url");
  const expected = signature(payload, configured);
  if (received.length !== expected.length || !timingSafeEqual(received, expected)) return undefined;
  try {
    const binding = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    if (
      typeof binding?.roomName !== "string" || !/^conversationalist-[0-9a-f-]{36}$/.test(binding.roomName)
      || typeof binding?.identity !== "string" || !/^person-[0-9a-f-]{36}$/.test(binding.identity)
    ) return undefined;
    return { roomName: binding.roomName, identity: binding.identity };
  } catch {
    return undefined;
  }
}
