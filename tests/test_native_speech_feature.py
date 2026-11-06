"""SDK-reference tests for content-free synthesized-speech gain."""

from array import array

import pytest

from forge_conversationalist.native_speech_feature import NativeSpeechFeatureAnalyzer


def test_native_gain_matches_pinned_sdk_for_activity_release_and_reentry():
    analyzer = NativeSpeechFeatureAnalyzer(24000)
    samples = (0, 8192, 8192, 328, 33, 33, 33, 33, 33, 8192)
    expected = (0, 1.248, 1.4118, 0.5820351871516313, 0, 0, 0, 0, 0, 1.4316932527401562)
    payload = array("h", [value for sample in samples for value in [sample] * 1200])
    # Arbitrary provider chunk boundaries must not reset the analysis window.
    output = []
    for offset in range(0, len(payload), 437):
        output.extend(analyzer.feed_pcm16(payload[offset:offset + 437]))
    assert [hop.sequence for hop in output] == list(range(len(samples)))
    assert [hop.gain for hop in output] == pytest.approx(expected, abs=1e-12)
    packet = output[2].as_wire(stream_id="synth-1")
    assert packet == {
        "schema": "forge.native_speech_gain.v1", "stream_id": "synth-1",
        "sequence": 2, "offset_ms": 100, "duration_ms": 50,
        "gain": pytest.approx(1.4118), "timing_source": "generated_pcm",
        "acoustic_playout_measured": False,
    }
    assert "pcm" not in packet and "transcript" not in packet and "pose" not in packet


def test_native_gain_rejects_invalid_formats_and_streams():
    with pytest.raises(ValueError):
        NativeSpeechFeatureAnalyzer(0)
    analyzer = NativeSpeechFeatureAnalyzer(24000, channels=2)
    with pytest.raises(ValueError, match="channel-aligned"):
        analyzer.feed_pcm16(array("h", [1]))
    with pytest.raises(TypeError):
        analyzer.feed_pcm16(None)
    hop = analyzer.feed_pcm16(array("h", [8192] * 2400))[0]
    with pytest.raises(ValueError):
        hop.as_wire(stream_id="")
