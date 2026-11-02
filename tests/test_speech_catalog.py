from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from forge_conversationalist import speech_catalog


def test_public_catalog_filters_by_speech_and_character_or_byte_price(monkeypatch) -> None:
    monkeypatch.setattr(speech_catalog, "_catalog_cache", None)
    monkeypatch.setattr(
        speech_catalog,
        "httpx",
        SimpleNamespace(
            get=Mock(
                return_value=SimpleNamespace(
                    raise_for_status=Mock(),
                    json=lambda: {
                        "data": [
                            {
                                "id": "qwen/eligible",
                                "name": "Qwen Eligible",
                                "architecture": {"output_modalities": ["speech"]},
                                "pricing": {"prompt": "0.00002", "completion": "0"},
                                "supported_voices": ["z", "a", "a"],
                            },
                            {
                                "id": "fish-audio/drama-3",
                                "architecture": {"output_modalities": ["speech"]},
                                "pricing": {"prompt": "0.000002", "completion": "0"},
                                "supported_voices": None,
                            },
                            {
                                "id": "google/token-priced",
                                "architecture": {"output_modalities": ["speech"]},
                                "pricing": {"prompt": "0.0000005", "completion": "0.000006"},
                                "supported_voices": ["voice"],
                            },
                            {
                                "id": "seed/duration-priced",
                                "architecture": {"output_modalities": ["speech"]},
                                "pricing": {"prompt": "0", "completion": "0.0025"},
                                "supported_voices": ["voice"],
                            },
                            {
                                "id": "expensive/tts",
                                "architecture": {"output_modalities": ["speech"]},
                                "pricing": {"prompt": "0.000021", "completion": "0"},
                                "supported_voices": ["voice"],
                            },
                            {
                                "id": "llm/not-speech",
                                "architecture": {"output_modalities": ["text"]},
                                "pricing": {"prompt": "0", "completion": "0"},
                                "supported_voices": ["voice"],
                            },
                        ]
                    },
                )
            )
        ),
    )

    models = speech_catalog.affordable_speech_models()
    assert {model["id"] for model in models} == {
        "qwen/eligible",
        "fish-audio/drama-3",
    }
    qwen = next(model for model in models if model["id"] == "qwen/eligible")
    assert qwen["pricePerMillion"] == 20
    assert qwen["priceUnit"] == "characters"
    assert qwen["voices"] == ["a", "z"]
    assert qwen["selectable"] is True
    fish = next(model for model in models if model["id"] == "fish-audio/drama-3")
    assert fish["pricePerMillion"] == 2
    assert fish["priceUnit"] == "bytes"
    assert fish["selectable"] is False


def test_catalog_cache_and_model_voice_pair_validation(monkeypatch) -> None:
    monkeypatch.setattr(speech_catalog, "_catalog_cache", None)
    fetch = Mock(
        return_value=SimpleNamespace(
            raise_for_status=Mock(),
            json=lambda: {
                "data": [
                    {
                        "id": "qwen/model",
                        "name": "Qwen Model",
                        "architecture": {"output_modalities": ["speech"]},
                        "pricing": {"prompt": "0.000015", "completion": "0"},
                        "supported_voices": ["loongjohn"],
                    }
                ]
            },
        )
    )
    monkeypatch.setattr(speech_catalog, "httpx", SimpleNamespace(get=fetch))

    assert speech_catalog.selected_speech_model("qwen/model", "loongjohn") is not None
    assert speech_catalog.selected_speech_model("qwen/model", "invalid") is None
    assert speech_catalog.selected_speech_model("qwen/other", "loongjohn") is None
    speech_catalog.affordable_speech_models()
    assert fetch.call_count == 1
