import { NextRequest, NextResponse } from "next/server";
import { timingSafeEqual } from "node:crypto";
import { randomUUID } from "node:crypto";
import { RoomAgentDispatch, RoomConfiguration } from "@livekit/protocol";
import { AccessToken } from "livekit-server-sdk";

export const runtime = "nodejs";

const AGENT_NAME = "forge-conversationalist";

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
    agents: [new RoomAgentDispatch({ agentName: AGENT_NAME })],
  });

  const token = await accessToken.toJwt();
  return NextResponse.json(
    { token, roomName, serverUrl: url },
    { headers: { "Cache-Control": "no-store" } },
  );
}
