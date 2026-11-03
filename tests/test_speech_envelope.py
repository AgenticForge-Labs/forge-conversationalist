"""Hardware-free tests for the generic synthesized-audio envelope."""

from array import array
from types import SimpleNamespace

import pytest

from forge_conversationalist.speech_envelope import HOP_MS, SpeechEnvelopeAnalyzer


def pcm(*, sample: int, hops: int = 1, channels: int = 1) -> bytes:
    return array("h", [sample] * (800 * hops * channels)).tobytes()


def test_activity_and_intensity_follow_output_pcm_without_transcript():
    analyzer = SpeechEnvelopeAnalyzer(16000)
    first = analyzer.feed_pcm16(pcm(sample=16000, hops=3))
    assert len(first) == 3
    assert [h.offset_ms for h in first] == [0, 50, 100]
    assert all(h.active for h in first)
    assert 0 < first[0].intensity < first[-1].intensity <= 1
    assert all(-15 < h.rms_dbfs < 0 for h in first)
    packet = first[0].as_wire(stream_id="out-1")
    assert packet == {
        "schema": "forge.speech_envelope.v1",
        "stream_id": "out-1",
        "sequence": 0,
        "offset_ms": 0,
        "duration_ms": HOP_MS,
        "active": True,
        "intensity": first[0].intensity,
        "rms_dbfs": first[0].rms_dbfs,
        "timing_source": "generated_pcm",
        "acoustic_playout_measured": False,
    }
    assert "samples" not in packet and "transcript" not in packet


def test_vad_release_and_envelope_decay_on_silence():
    analyzer = SpeechEnvelopeAnalyzer(16000)
    loud = analyzer.feed_pcm16(pcm(sample=16000, hops=4))
    quiet = analyzer.feed_pcm16(pcm(sample=0, hops=8))
    assert loud[-1].active is True
    assert quiet[-1].active is False
    assert quiet[-1].intensity == 0.0
    assert quiet[0].sequence == 4


def test_split_audio_frames_preserve_hop_alignment_and_reset():
    analyzer = SpeechEnvelopeAnalyzer(16000)
    half = array("h", [12000] * 400)
    assert analyzer.feed_pcm16(half) == []
    hops = analyzer.feed_pcm16(half)
    assert len(hops) == 1 and hops[0].sequence == 0
    analyzer.reset()
    again = analyzer.feed_frame(SimpleNamespace(
        sample_rate=16000, num_channels=1, data=pcm(sample=12000),
    ))
    assert len(again) == 1 and again[0].offset_ms == 0


def test_stereo_downmix_and_format_rejection():
    analyzer = SpeechEnvelopeAnalyzer(16000, channels=2)
    assert len(analyzer.feed_pcm16(pcm(sample=10000, channels=2))) == 1
    with pytest.raises(ValueError, match="format changed"):
        analyzer.feed_frame(SimpleNamespace(
            sample_rate=44100, num_channels=2, data=pcm(sample=10000, channels=2),
        ))
    with pytest.raises(ValueError, match="channel-aligned"):
        analyzer.feed_pcm16(array("h", [1]))
    with pytest.raises(TypeError):
        analyzer.feed_pcm16("not PCM")


@pytest.mark.parametrize("rate, channels", [(0, 1), (16000, 0), (16000, 3), (True, 1)])
def test_invalid_audio_format_fails_closed(rate, channels):
    with pytest.raises(ValueError):
        SpeechEnvelopeAnalyzer(rate, channels)


def test_stream_id_is_required_and_bounded():
    hop = SpeechEnvelopeAnalyzer(16000).feed_pcm16(pcm(sample=10000))[0]
    with pytest.raises(ValueError):
        hop.as_wire(stream_id="")
    with pytest.raises(ValueError):
        hop.as_wire(stream_id="x" * 129)
