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


@pytest.mark.anyio
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


@pytest.mark.anyio
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


@pytest.mark.anyio
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


@pytest.mark.anyio
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


@pytest.mark.anyio
async def test_native_gain_passes_audio_through_and_is_independent_of_generic_sink():
    original = [frame(4), frame(4), frame(2)]
    generic, native = [], []
    output = [item async for item in observe_synthesized_audio(
        source(original), lambda batch: generic.append(batch) or False,
        stream_id="same-utterance", native_sink=lambda batch: native.append(batch) or True,
    )]
    assert all(a is b for a, b in zip(output, original, strict=True))
    assert len(generic) == 1
    assert [len(batch) for batch in native] == [1] * 10
    hops = [hop for batch in native for hop in batch]
    assert [hop["sequence"] for hop in hops] == list(range(10))
    assert all(hop["schema"] == "forge.native_speech_gain.v1" for hop in hops)
    assert all(hop["stream_id"] == "same-utterance" for hop in hops)
    assert all("data" not in hop and "text" not in hop and "pose" not in hop for hop in hops)


@pytest.mark.anyio
async def test_rejected_native_gain_does_not_disable_generic_features():
    original = [frame(4), frame(4)]
    generic, native = [], []
    output = [item async for item in observe_synthesized_audio(
        source(original), lambda batch: generic.append(batch) or True,
        stream_id="utterance", native_sink=lambda batch: native.append(batch) or False,
    )]
    assert output == original
    assert len(native) == 1
    assert len(generic) == 2


class FakeAgent:
    class default:
        @staticmethod
        async def tts_node(agent, _text, _settings):
            for item in agent.frames:
                yield item

    def __init__(self, frames):
        self.frames = frames


@pytest.mark.anyio
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



@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_tap_emits_wire_hops_for_synthesized_frames():
    """The opt-in tap must actually produce envelope hops from TTS frames."""
    import asyncio

    from forge_conversationalist.speech_envelope_tap import observe_synthesized_audio

    class Frame:
        def __init__(self, data):
            self.data, self.sample_rate, self.num_channels = data, 24000, 1

    async def frames():
        for _ in range(12):
            yield Frame(b"\x10\x20" * 1200)  # 50 ms of 24 kHz mono

    batches = []

    def sink(batch):
        batches.append(batch)
        return True

    async def run():
        seen = [f async for f in observe_synthesized_audio(
            frames(), sink, stream_id="test-stream",
        )]
        return seen

    seen = asyncio.run(run())
    assert len(seen) == 12, "every audio frame must pass through unchanged"
    hops = [hop for batch in batches for hop in batch]
    assert hops, "the tap produced no envelope hops"
    assert hops[0]["schema"] == "forge.speech_envelope.v1"
    assert hops[0]["stream_id"] == "test-stream"
    assert [h["sequence"] for h in hops] == list(range(len(hops)))
    assert all(h["timing_source"] == "generated_pcm" for h in hops)
    assert all(h["acoustic_playout_measured"] is False for h in hops)
