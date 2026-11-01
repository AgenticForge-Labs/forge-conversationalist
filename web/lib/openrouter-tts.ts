export type OpenRouterTtsModel = {
  id: string;
  name: string;
  pricePerMillion: number;
  priceUnit: "characters" | "bytes";
  voices: string[];
  selectable: boolean;
  responseFormat: "mp3" | "pcm";
  sampleRate: number;
  formatVerified: boolean;
};

const CATALOG_URL = "https://openrouter.ai/api/v1/models?output_modalities=speech";
const MAX_PRICE_PER_UNIT = 0.00002;
const BYTE_PRICED_MODELS = new Set(["fish-audio/drama-3"]);
const FORMAT_OVERRIDES: Record<string, { format: "mp3" | "pcm"; sampleRate: number }> = {
  "mistralai/voxtral-mini-tts-2603": { format: "mp3", sampleRate: 24000 },
  "heygen/heygen-voice-1": { format: "pcm", sampleRate: 44100 },
  "heygen/heygen-voice-1-turbo": { format: "pcm", sampleRate: 44100 },
  "heygen/heygen-voice-1-turbo:free": { format: "pcm", sampleRate: 44100 },
};

export async function fetchAffordableSpeechModels(): Promise<OpenRouterTtsModel[]> {
  const response = await fetch(CATALOG_URL, { next: { revalidate: 300 } });
  if (!response.ok) throw new Error(`OpenRouter catalog returned HTTP ${response.status}`);
  const payload: unknown = await response.json();
  if (!payload || typeof payload !== "object" || !("data" in payload) || !Array.isArray(payload.data)) {
    throw new Error("OpenRouter speech catalog has an invalid shape");
  }

  return payload.data.flatMap((raw: unknown) => {
    if (!raw || typeof raw !== "object") return [];
    const item = raw as Record<string, unknown>;
    const id = item.id;
    const architecture = item.architecture as Record<string, unknown> | undefined;
    const pricing = item.pricing as Record<string, unknown> | undefined;
    if (typeof id !== "string" || !Array.isArray(architecture?.output_modalities)
      || !architecture.output_modalities.includes("speech") || !pricing) return [];
    const rawInputPrice = pricing.prompt;
    const rawOutputPrice = pricing.completion;
    if ((typeof rawInputPrice !== "string" && typeof rawInputPrice !== "number")
      || (typeof rawOutputPrice !== "string" && typeof rawOutputPrice !== "number")
      || (typeof rawInputPrice === "string" && !rawInputPrice.trim())
      || (typeof rawOutputPrice === "string" && !rawOutputPrice.trim())) return [];
    const inputPrice = Number(rawInputPrice);
    const outputPrice = Number(rawOutputPrice);
    if (!Number.isFinite(inputPrice) || inputPrice < 0 || inputPrice > MAX_PRICE_PER_UNIT
      || outputPrice !== 0) return [];
    const rawVoices = item.supported_voices;
    const voices = Array.isArray(rawVoices)
      ? [...new Set(rawVoices.filter((voice): voice is string => typeof voice === "string"))].sort()
      : [];
    const format = FORMAT_OVERRIDES[id];
    return [{
      id,
      name: typeof item.name === "string" ? item.name : id,
      pricePerMillion: inputPrice * 1_000_000,
      priceUnit: BYTE_PRICED_MODELS.has(id) ? "bytes" as const : "characters" as const,
      voices,
      selectable: voices.length > 0,
      responseFormat: format?.format ?? "mp3",
      sampleRate: format?.sampleRate ?? 24_000,
      formatVerified: Boolean(format),
    }];
  }).sort((a, b) => a.pricePerMillion - b.pricePerMillion || a.name.localeCompare(b.name));
}
