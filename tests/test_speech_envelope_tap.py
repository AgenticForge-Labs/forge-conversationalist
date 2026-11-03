"""Offline validation of the opt-in passthrough outgoing TTS observation."""

from array import array
from dataclasses import dataclass

import pytest

from forge_conversationalist.speech_envelope_tap import (
    agent_class_with_speech_envelope,
    observe_synthesized_audio,
)


@dataclass
class FakeAudioFrame:
    sample_rate: int
    num_channels: int
    data: bytes


def frame(hops: int = 1, amplitude: int = 12000) -> FakeAudioFrame:
    return FakeAudioFrame(16000, 1, array("h", [amplitude] * (800 * hops)).tobytes())


async def source(frames):
    for item in frames:
        yield item


@pytest.mark.asyncio
async def test_pass_through_identity_and_no_private_content():
    frames = [frame(2), frame(6)]
    batches = []
    async def receive():
        return [
            output async for output in observe_synthesized_audio(
                source(frames), lambda batch: batches.append(batch) or True,
                stream_id="tts-123",
            )
        ]
    observed = await receive()
    assert all(a is b for a, b in zip(observed, frames, strict=True))
    assert [len(b) for b in batches] == [4, 4]
    assert [x["sequence"] for b in batches for x in b] == list(range(8))
    assert {x["timing_source"] for b in batches for x in b} == {"generated_pcm"}
    assert {x["stream_id"] for b in batches for x in b} == {"tts-123"}
    assert all(x["acoustic_playout_measured"] is False for b in batches for x in b)
    assert all("data" not in x and "text" not in x for b in batches for x in b)


@pytest.mark.asyncio
async def test_refused_optional_feature_batch_never_blocks_voice():
    original = [frame(4), frame(4), frame(4)]
    emitted = []
    outputs = [
        x async for x in observe_synthesized_audio(
            source(original), lambda batch: emitted.append(batch) or False,
            stream_id="tts-refused",
        )
    ]
    assert outputs == original
    assert all(x is y for x, y in zip(outputs, original, strict=True))
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_format_failure_does_not_change_audio_frames():
    original = [frame(4), FakeAudioFrame(999, 1, b"bad"), frame(4)]
    captured = []
    outputs = [
        x async for x in observe_synthesized_audio(
            source(original), lambda batch: captured.append(batch) or True,
            stream_id="tts-invalid",
        )
    ]
    assert all(x is y for x, y in zip(outputs, original, strict=True))
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_optional_sink_exception_does_not_interrupt_voice():
    def broken(_):
        raise RuntimeError("unavailable")
    frames = [frame(4), frame(4)]
    observed = [
        f async for f in observe_synthesized_audio(
            source(frames), broken, stream_id="tts-exception",
        )
    ]
    assert observed == frames


class FakeAgent:
    class default:
        @staticmethod
        async def tts_node(agent, _text, _settings):
            for item in agent.frames:
                yield item

    def __init__(self, frames):
        self.frames = frames


@pytest.mark.asyncio
async def test_default_livekit_tts_node_is_not_replaced():
    captured = []
    WrappedAgent = agent_class_with_speech_envelope(
        FakeAgent, lambda batches: captured.extend(batches) or True,
    )
    items = [frame(2), frame(2), frame(2)]
    instance = WrappedAgent(items)
    output = [item async for item in instance.tts_node(None, None)]
    assert all(x is y for x, y in zip(output, items, strict=True))
    assert len(captured) == 6
    assert all(x["stream_id"].startswith("tts-") for x in captured)
    assert [x["offset_ms"] for x in captured] == [0, 50, 100, 150, 200, 250]


def test_sink_must_be_callable():
    with pytest.raises(TypeError):
        agent_class_with_speech_envelope(FakeAgent, None)
