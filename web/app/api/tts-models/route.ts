import { NextResponse } from "next/server";
import { fetchAffordableSpeechModels } from "@/lib/openrouter-tts";

export const runtime = "nodejs";

export async function GET() {
  try {
    const models = await fetchAffordableSpeechModels();
    return NextResponse.json({ models }, {
      headers: { "Cache-Control": "public, max-age=300, stale-while-revalidate=600" },
    });
  } catch {
    return NextResponse.json(
      { error: "Could not load OpenRouter's speech model catalog." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
