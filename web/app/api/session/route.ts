import { NextRequest, NextResponse } from "next/server";
import { timingSafeEqual, randomUUID } from "node:crypto";
import { ParticipantInfo_Kind, RoomAgentDispatch, RoomConfiguration } from "@livekit/protocol";
import { AccessToken, RoomServiceClient } from "livekit-server-sdk";
import { fetchAffordableSpeechModels } from "@/lib/openrouter-tts";
import { decodeSessionBinding, encodeSessionBinding } from "@/lib/session-binding.mjs";
import { allowsLocalAutoLogin } from "@/lib/local-auto-login.mjs";

export const runtime = "nodejs";

const AGENT_NAME = "forge-conversationalist";
const VAD_SENSITIVITIES = ["less", "standard", "more"] as const;
const TURN_PACINGS = ["quick", "standard", "patient"] as const;
const ACCESS_COOKIE = "forge_conversationalist_access";
const SESSION_COOKIE = "forge_conversationalist_session";
const ACCESS_COOKIE_AGE = 60 * 60 * 24 * 30;

function liveKitHost(url: string): string {
  return url.startsWith("wss://") ? `https://${url.slice(6)}` : `http://${url.slice(5)}`;
}

function cookieOptions(request: NextRequest) {
  return {
    httpOnly: true,
    sameSite: "strict" as const,
    secure: request.nextUrl.protocol === "https:",
    path: "/api/session",
    maxAge: ACCESS_COOKIE_AGE,
  };
}

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
  if (allowsLocalAutoLogin({
    enabled: process.env.CONVERSATIONALIST_LOCAL_AUTO_LOGIN,
    nodeEnv: process.env.NODE_ENV,
    hostname: request.nextUrl.hostname,
    fetchSite: request.headers.get("sec-fetch-site"),
  })) {
    const response = NextResponse.json({ authorized: true }, { headers: { "Cache-Control": "no-store" } });
    response.cookies.set(ACCESS_COOKIE, configured, cookieOptions(request));
    return response;
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
  let requestedTurnPacing: unknown;
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
      if (body && typeof body === "object" && "turnPacing" in body) {
        requestedTurnPacing = body.turnPacing;
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
  if (requestedTurnPacing !== undefined && !TURN_PACINGS.includes(requestedTurnPacing as typeof TURN_PACINGS[number])) {
    return NextResponse.json({ error: "Unsupported turn pacing" }, { status: 400 });
  }

  // The signed HttpOnly cookie binds an authenticated browser to its existing
  // room and identity. The browser never supplies a room or identity itself.
  const liveKit = new RoomServiceClient(liveKitHost(url), key, secret);
  const binding = cookieAuthorized
    ? decodeSessionBinding(request.cookies.get(SESSION_COOKIE)?.value ?? "", configured)
    : undefined;
  let roomName = binding?.roomName;
  let identity = binding?.identity;
  let resumeExistingRoom = false;
  if (roomName && identity) {
    try {
      resumeExistingRoom = (await liveKit.listRooms([roomName])).some((room) => room.name === roomName);
      if (resumeExistingRoom) {
        const participants = await liveKit.listParticipants(roomName);
        if (!participants.some((participant) => participant.kind === ParticipantInfo_Kind.AGENT)) {
          return NextResponse.json(
            { error: "The agent is no longer connected. End this conversation and start a new one." },
            { status: 503, headers: { "Cache-Control": "no-store" } },
          );
        }
      }
    } catch {
      return NextResponse.json({ error: "Unable to check the existing conversation" }, { status: 503 });
    }
  }
  if (!resumeExistingRoom) {
    roomName = `conversationalist-${randomUUID()}`;
    identity = `person-${randomUUID()}`;
  }
  const sessionRoom = roomName!;
  const sessionIdentity = identity!;
  const accessToken = new AccessToken(key, secret, { identity: sessionIdentity, ttl: "15m" });
  accessToken.addGrant({
    roomJoin: true,
    room: sessionRoom,
    canPublish: true,
    canSubscribe: true,
    canPublishData: true,
  });
  const dispatchMetadata = {
    ...(requestedSpeechModel ? { tts_model: requestedSpeechModel.id } : {}),
    ...(requestedVoice ? { tts_voice: requestedVoice } : {}),
    ...(requestedVadSensitivity ? { vad_sensitivity: requestedVadSensitivity } : {}),
    ...(requestedTurnPacing ? { turn_pacing: requestedTurnPacing } : {}),
    authorized_identity: sessionIdentity,
  };
  if (!resumeExistingRoom) {
    accessToken.roomConfig = new RoomConfiguration({
      agents: [new RoomAgentDispatch({
        agentName: AGENT_NAME,
        metadata: JSON.stringify(dispatchMetadata),
      })],
    });
  }

  const token = await accessToken.toJwt();
  const response = NextResponse.json(
    { token, roomName: sessionRoom, serverUrl: url, resumed: resumeExistingRoom },
    { headers: { "Cache-Control": "no-store" } },
  );
  if (bearerAuthorized) {
    response.cookies.set(ACCESS_COOKIE, configured, cookieOptions(request));
  }
  response.cookies.set(SESSION_COOKIE, encodeSessionBinding({ roomName: sessionRoom, identity: sessionIdentity }, configured), cookieOptions(request));
  return response;
}

export async function DELETE(request: NextRequest) {
  const configured = process.env.CONVERSATIONALIST_ACCESS_TOKEN;
  const key = process.env.LIVEKIT_API_KEY;
  const secret = process.env.LIVEKIT_API_SECRET;
  const url = process.env.LIVEKIT_URL;
  if (!configured || configured.length < 32 || !key || !secret || !url) {
    return NextResponse.json({ error: "Server is not configured" }, { status: 503 });
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
  const binding = decodeSessionBinding(request.cookies.get(SESSION_COOKIE)?.value ?? "", configured);
  if (binding) {
    try {
      const liveKit = new RoomServiceClient(liveKitHost(url), key, secret);
      const exists = (await liveKit.listRooms([binding.roomName])).some((room) => room.name === binding.roomName);
      if (exists) {
        try {
          await liveKit.deleteRoom(binding.roomName);
        } catch {
          // A concurrent End or worker cleanup may have deleted it already.
          const remains = (await liveKit.listRooms([binding.roomName])).some((room) => room.name === binding.roomName);
          if (remains) throw new Error("Room deletion failed");
        }
      }
    } catch {
      return NextResponse.json({ error: "Unable to end the conversation" }, { status: 503 });
    }
  }
  const response = NextResponse.json({ ended: true }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(SESSION_COOKIE, "", { ...cookieOptions(request), maxAge: 0 });
  return response;
}
