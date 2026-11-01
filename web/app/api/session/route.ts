import { NextRequest, NextResponse } from "next/server";
import { timingSafeEqual } from "node:crypto";
import { randomUUID } from "node:crypto";
import { RoomAgentDispatch, RoomConfiguration } from "@livekit/protocol";
import { AccessToken } from "livekit-server-sdk";
import { fetchAffordableSpeechModels } from "@/lib/openrouter-tts";

export const runtime = "nodejs";

const AGENT_NAME = "forge-conversationalist";
const VAD_SENSITIVITIES = ["less", "standard", "more"] as const;
const ACCESS_COOKIE = "forge_conversationalist_access";
const ACCESS_COOKIE_AGE = 60 * 60 * 24 * 30;

function authorized(received: string, configured: string): boolean {
  const left = Buffer.from(received, "utf8");
  const right = Buffer.from(configured, "utf8");
  // Length mismatch must never pass. No logging either value.
  return left.length === right.length && timingSafeEqual(left, right);
}

function requestHasValidCookie(request: NextRequest, configured: string): boolean {
  const cookie = request.cookies.get(ACCESS_COOKIE)?.value ?? "";
  return authorized(cookie, configured);
}

export async function GET(request: NextRequest) {
  const configured = process.env.CONVERSATIONALIST_ACCESS_TOKEN;
  if (!configured || configured.length < 32) {
    return NextResponse.json({ error: "Server is not configured" }, { status: 503 });
  }
  if (!requestHasValidCookie(request, configured)) {
    return NextResponse.json({ authorized: false }, { status: 401, headers: { "Cache-Control": "no-store" } });
  }
  return NextResponse.json({ authorized: true }, { headers: { "Cache-Control": "no-store" } });
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
  const bearerAuthorized = bearer.startsWith("Bearer ") && authorized(bearer.slice(7), configured);
  const cookieAuthorized = requestHasValidCookie(request, configured);
  if (!bearerAuthorized && !cookieAuthorized) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const requestOrigin = request.headers.get("origin");
  const expectedOrigin = `${request.nextUrl.protocol}//${request.headers.get("host") ?? ""}`;
  if (cookieAuthorized && !bearerAuthorized && requestOrigin !== expectedOrigin) {
    return NextResponse.json({ error: "Cross-origin request denied" }, { status: 403 });
  }
  let requestedVoice: unknown;
  let requestedModel: unknown;
  let requestedVadSensitivity: unknown;
  const rawBody = await request.text();
  if (rawBody) {
    try {
      const body: unknown = JSON.parse(rawBody);
      if (body && typeof body === "object" && "ttsVoice" in body) {
        requestedVoice = body.ttsVoice;
      }
      if (body && typeof body === "object" && "ttsModel" in body) {
        requestedModel = body.ttsModel;
      }
      if (body && typeof body === "object" && "vadSensitivity" in body) {
        requestedVadSensitivity = body.vadSensitivity;
      }
    } catch {
      return NextResponse.json({ error: "Invalid request" }, { status: 400 });
    }
  }
  let requestedSpeechModel: Awaited<ReturnType<typeof fetchAffordableSpeechModels>>[number] | undefined;
  if (requestedModel !== undefined) {
    if (typeof requestedModel !== "string") {
      return NextResponse.json({ error: "Unsupported speech model" }, { status: 400 });
    }
    let catalog;
    try {
      catalog = await fetchAffordableSpeechModels();
    } catch {
      return NextResponse.json({ error: "Speech model catalog is unavailable" }, { status: 503 });
    }
    requestedSpeechModel = catalog.find((model) => model.id === requestedModel);
    if (!requestedSpeechModel?.selectable) {
      return NextResponse.json({ error: "Speech model has no published supported voices" }, { status: 400 });
    }
    if (typeof requestedVoice !== "string" || !requestedSpeechModel.voices.includes(requestedVoice)) {
      return NextResponse.json({ error: "Voice is not supported by the selected speech model" }, { status: 400 });
    }
  } else if (requestedVoice !== undefined) {
    const legacyVoices = ["loongjohn", "longanhuan_v3.6"];
    if (typeof requestedVoice !== "string" || !legacyVoices.includes(requestedVoice)) {
      return NextResponse.json({ error: "Unsupported voice" }, { status: 400 });
    }
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
  const dispatchMetadata = {
    ...(requestedSpeechModel ? { tts_model: requestedSpeechModel.id } : {}),
    ...(requestedVoice ? { tts_voice: requestedVoice } : {}),
    ...(requestedVadSensitivity ? { vad_sensitivity: requestedVadSensitivity } : {}),
    authorized_identity: identity,
  };
  accessToken.roomConfig = new RoomConfiguration({
    agents: [new RoomAgentDispatch({
      agentName: AGENT_NAME,
      metadata: JSON.stringify(dispatchMetadata),
    })],
  });

  const token = await accessToken.toJwt();
  const response = NextResponse.json(
    { token, roomName, serverUrl: url },
    { headers: { "Cache-Control": "no-store" } },
  );
  if (bearerAuthorized) {
    response.cookies.set(ACCESS_COOKIE, configured, {
      httpOnly: true,
      sameSite: "strict",
      secure: request.nextUrl.protocol === "https:",
      path: "/api/session",
      maxAge: ACCESS_COOKIE_AGE,
    });
  }
  return response;
}
