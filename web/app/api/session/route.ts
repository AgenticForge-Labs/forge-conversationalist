import { NextRequest, NextResponse } from "next/server";
import { timingSafeEqual } from "node:crypto";
import { randomUUID } from "node:crypto";
import { RoomAgentDispatch, RoomConfiguration } from "@livekit/protocol";
import { AccessToken } from "livekit-server-sdk";

export const runtime = "nodejs";

const AGENT_NAME = "forge-conversationalist";
const OPENROUTER_QWEN_VOICES = ["loongjohn", "longanhuan_v3.6"] as const;
const VAD_SENSITIVITIES = ["less", "standard", "more"] as const;

function authorized(received: string, configured: string): boolean {
  const left = Buffer.from(received, "utf8");
  const right = Buffer.from(configured, "utf8");
  // Length mismatch must never pass. No logging either value.
  return left.length === right.length && timingSafeEqual(left, right);
}

export async function POST(request: NextRequest) {
  const configured = process.env.CONVERSATIONALIST_ACCESS_TOKEN;
  const key = process.env.LIVEKIT_API_KEY;
  const secret = process.env.LIVEKIT_API_SECRET;
  const url = process.env.LIVEKIT_URL;

  // No insecure fallback. Do not deploy publicly with this development gate.
  if (!configured || configured.length < 32 || !key || !secret || !url) {
    return NextResponse.json({ error: "Server is not configured" }, { status: 503 });
  }
  if (!(url.startsWith("wss://") || url.startsWith("ws://localhost:"))) {
    return NextResponse.json({ error: "Invalid server configuration" }, { status: 503 });
  }
  const bearer = request.headers.get("authorization") ?? "";
  if (!bearer.startsWith("Bearer ") || !authorized(bearer.slice(7), configured)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  let requestedVoice: unknown;
  let requestedVadSensitivity: unknown;
  const rawBody = await request.text();
  if (rawBody) {
    try {
      const body: unknown = JSON.parse(rawBody);
      if (body && typeof body === "object" && "ttsVoice" in body) {
        requestedVoice = body.ttsVoice;
      }
      if (body && typeof body === "object" && "vadSensitivity" in body) {
        requestedVadSensitivity = body.vadSensitivity;
      }
    } catch {
      return NextResponse.json({ error: "Invalid request" }, { status: 400 });
    }
  }
  if (
    requestedVoice !== undefined
    && !OPENROUTER_QWEN_VOICES.includes(requestedVoice as typeof OPENROUTER_QWEN_VOICES[number])
  ) {
    return NextResponse.json({ error: "Unsupported voice" }, { status: 400 });
  }
  if (
    requestedVadSensitivity !== undefined
    && !VAD_SENSITIVITIES.includes(requestedVadSensitivity as typeof VAD_SENSITIVITIES[number])
  ) {
    return NextResponse.json({ error: "Unsupported VAD sensitivity" }, { status: 400 });
  }

  // Never accept arbitrary room/identity/agent values supplied by the browser.
  const roomName = `conversationalist-${randomUUID()}`;
  const identity = `person-${randomUUID()}`;
  const accessToken = new AccessToken(key, secret, { identity, ttl: "15m" });
  accessToken.addGrant({
    roomJoin: true,
    room: roomName,
    canPublish: true,
    canSubscribe: true,
    canPublishData: true,
  });
  accessToken.roomConfig = new RoomConfiguration({
    agents: [new RoomAgentDispatch({
      agentName: AGENT_NAME,
      metadata: requestedVoice || requestedVadSensitivity
        ? JSON.stringify({
            ...(requestedVoice ? { tts_voice: requestedVoice } : {}),
            ...(requestedVadSensitivity ? { vad_sensitivity: requestedVadSensitivity } : {}),
          })
        : "",
    })],
  });

  const token = await accessToken.toJwt();
  return NextResponse.json(
    { token, roomName, serverUrl: url },
    { headers: { "Cache-Control": "no-store" } },
  );
}
