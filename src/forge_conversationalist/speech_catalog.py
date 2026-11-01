"""OpenRouter's public catalog entries usable by the voice selector."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from threading import Lock
from time import monotonic
from typing import Any

import httpx

CATALOG_URL = "https://openrouter.ai/api/v1/models?output_modalities=speech"
MAX_PRICE_PER_UNIT = Decimal("0.00002")
# OpenRouter documents these prices in bytes; other eligible speech entries
# in the catalog use input characters. Never show byte rates as character rates.
BYTE_PRICED_MODELS = frozenset({"fish-audio/drama-3"})
# Verified format constraints documented by OpenRouter. Other formats are
# displayed as unverified and the worker uses the configured MP3 default.
FORMAT_OVERRIDES = {
    "mistralai/voxtral-mini-tts-2603": ("mp3", 24000),
    "heygen/heygen-voice-1": ("pcm", 44100),
    "heygen/heygen-voice-1-turbo": ("pcm", 44100),
    "heygen/heygen-voice-1-turbo:free": ("pcm", 44100),
}
CATALOG_TTL_SECONDS = 300
_catalog_lock = Lock()
_catalog_cache: tuple[dict[str, Any], ...] | None = None
_catalog_cached_at = 0.0


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() and result >= 0 else None


def affordable_speech_models() -> tuple[dict[str, Any], ...]:
    """Fetch models priced at <= $20/M chars or bytes, refreshing every 5 min."""
    global _catalog_cache, _catalog_cached_at
    with _catalog_lock:
        now = monotonic()
        if _catalog_cache is not None and now - _catalog_cached_at < CATALOG_TTL_SECONDS:
            return _catalog_cache
        response = httpx.get(CATALOG_URL, timeout=8.0)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise TypeError("OpenRouter speech catalog has an invalid shape")

        entries: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            pricing = item.get("pricing")
            modalities = (item.get("architecture") or {}).get("output_modalities")
            if not isinstance(model_id, str) or "speech" not in (modalities or []):
                continue
            if not isinstance(pricing, dict):
                continue
            input_price = _decimal(pricing.get("prompt"))
            output_price = _decimal(pricing.get("completion"))
            # Token/output priced entries and duration-priced entries do not have
            # an honest per-character/byte rate in this catalog representation.
            if input_price is None or output_price != 0 or input_price > MAX_PRICE_PER_UNIT:
                continue
            unit = "bytes" if model_id in BYTE_PRICED_MODELS else "characters"
            price = input_price * 1_000_000
            raw_voices = item.get("supported_voices")
            voices = sorted({v for v in raw_voices if isinstance(v, str)}) if isinstance(raw_voices, list) else []
            format_info = FORMAT_OVERRIDES.get(model_id)
            entries.append(
                {
                    "id": model_id,
                    "name": item.get("name") if isinstance(item.get("name"), str) else model_id,
                    "pricePerMillion": float(price),
                    "priceUnit": unit,
                    "voices": voices,
                    "selectable": bool(voices),
                    "responseFormat": format_info[0] if format_info else "mp3",
                    "sampleRate": format_info[1] if format_info else 24000,
                    "formatVerified": format_info is not None,
                }
            )
        _catalog_cache = tuple(sorted(entries, key=lambda model: (model["pricePerMillion"], model["name"].casefold())))
        _catalog_cached_at = now
        return _catalog_cache


def selected_speech_model(model_id: Any, voice: Any) -> dict[str, Any] | None:
    """Return a supported model/voice pair or None; callers fail closed."""
    if not isinstance(model_id, str) or not isinstance(voice, str):
        return None
    model = next((m for m in affordable_speech_models() if m["id"] == model_id), None)
    if model is None or not model["selectable"] or voice not in model["voices"]:
        return None
    return model
