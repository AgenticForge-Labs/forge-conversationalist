import { NextRequest, NextResponse } from "next/server";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { allowsLocalAutoLogin } from "@/lib/local-auto-login.mjs";

export const runtime = "nodejs";

const run = promisify(execFile);
const ACCESS_COOKIE = "forge_conversationalist_access";

function authorized(request: NextRequest): boolean {
  const configured = process.env.CONVERSATIONALIST_ACCESS_TOKEN ?? "";
  if (configured.length < 32) return false;
  const cookie = request.cookies.get(ACCESS_COOKIE)?.value ?? "";
  return cookie.length === configured.length && cookie === configured;
}

/** Capture-device control is a host operation; loopback development only. */
function allowed(request: NextRequest): boolean {
  const loopback =
    request.nextUrl.hostname === "localhost" || request.nextUrl.hostname === "127.0.0.1";
  const dev = process.env.NODE_ENV === "development";
  const auto = allowsLocalAutoLogin({
    enabled: process.env.CONVERSATIONALIST_LOCAL_AUTO_LOGIN,
    nodeEnv: process.env.NODE_ENV,
    hostname: request.nextUrl.hostname,
    fetchSite: request.headers.get("sec-fetch-site"),
  });
  return loopback && dev && (auto || authorized(request));
}

async function defaultSource(): Promise<string> {
  const { stdout } = await run("pactl", ["get-default-source"], { timeout: 5000 });
  return stdout.trim();
}

async function readState(source: string): Promise<{ source: string; muted: boolean; volume: number }> {
  const [mute, vol] = await Promise.all([
    run("pactl", ["get-source-mute", source], { timeout: 5000 }),
    run("pactl", ["get-source-volume", source], { timeout: 5000 }),
  ]);
  const muted = /Mute:\s*yes/i.test(mute.stdout);
  const match = vol.stdout.match(/(\d+)%/);
  return { source, muted, volume: match ? Number(match[1]) : 0 };
}

export async function GET(request: NextRequest) {
  if (!allowed(request)) {
    return NextResponse.json({ error: "Not available" }, { status: 403 });
  }
  try {
    const source = await defaultSource();
    return NextResponse.json(await readState(source), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: "Capture device unavailable" }, { status: 503 });
  }
}

export async function POST(request: NextRequest) {
  if (!allowed(request)) {
    return NextResponse.json({ error: "Not available" }, { status: 403 });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }
  if (!body || typeof body !== "object") {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }
  const { mute, volume } = body as { mute?: unknown; volume?: unknown };
  if (mute !== undefined && typeof mute !== "boolean") {
    return NextResponse.json({ error: "mute must be a boolean" }, { status: 400 });
  }
  if (
    volume !== undefined &&
    (typeof volume !== "number" || !Number.isFinite(volume) || volume < 0 || volume > 100)
  ) {
    return NextResponse.json({ error: "volume must be 0-100" }, { status: 400 });
  }
  try {
    const source = await defaultSource();
    if (typeof mute === "boolean") {
      await run("pactl", ["set-source-mute", source, mute ? "1" : "0"], { timeout: 5000 });
    }
    if (typeof volume === "number") {
      await run("pactl", ["set-source-volume", source, `${Math.round(volume)}%`], { timeout: 5000 });
    }
    return NextResponse.json(await readState(source), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: "Could not change the capture device" }, { status: 503 });
  }
}
