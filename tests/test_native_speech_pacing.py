"""Deterministic feature timing; no LiveKit sink or robot connection."""

import pytest

from forge_conversationalist.native_speech_pacing import NativeSpeechPacer


def hop(sequence, *, stream="s"):
    return {"schema": "forge.native_speech_gain.v1", "stream_id": stream,
            "sequence": sequence, "offset_ms": sequence * 50, "duration_ms": 50,
            "gain": 0.8, "timing_source": "generated_pcm",
            "acoustic_playout_measured": False}


def test_generated_audio_ahead_is_paced_for_sixteen_seconds():
    pacer = NativeSpeechPacer()
    pacer.bind("s")
    # Playback may begin before the analyzer has assembled its first batch.
    pacer.arm(stream_id="s", output_started_at=100.0)
    for start in range(0, 320, 8):
        assert pacer.offer([hop(i) for i in range(start, start + 8)])
    for i in range(320):
        now = 100.0 + i * 0.05 + 1e-9
        batch = pacer.take_due(now=now)
        assert [item["sequence"] for item in batch] == [i]
    assert not pacer.fenced
    assert pacer.take_due(now=116.0) == []


def test_envelope_generated_ahead_is_paced_for_entire_output():
    def envelope(i):
        return {"schema": "forge.speech_envelope.v1", "stream_id": "s",
                "sequence": i, "offset_ms": i * 50, "duration_ms": 50,
                "active": True, "intensity": 0.6, "rms_dbfs": -18.0,
                "timing_source": "generated_pcm", "acoustic_playout_measured": False}

    pacer = NativeSpeechPacer(feature_kind="envelope")
    pacer.bind("s")
    for start in range(0, 320, 8):
        assert pacer.offer([envelope(i) for i in range(start, start + 8)])
    assert pacer.take_due(now=100.0) == []
    pacer.arm(stream_id="s", output_started_at=100.0)
    for i in range(320):
        assert [f["sequence"] for f in pacer.take_due(now=100.0 + i * 0.05 + 1e-9)] == [i]
    assert not pacer.fenced


def test_envelope_pacing_rejects_raw_audio_and_invalid_fields_atomically():
    item = {"schema": "forge.speech_envelope.v1", "stream_id": "s",
            "sequence": 0, "offset_ms": 0, "duration_ms": 50,
            "active": True, "intensity": 0.6, "rms_dbfs": -18.0,
            "timing_source": "generated_pcm", "acoustic_playout_measured": False}
    pacer = NativeSpeechPacer(feature_kind="envelope")
    for bad in ({**item, "pcm": [0]}, {**item, "active": 1},
                {**item, "rms_dbfs": float("nan")}, {**item, "intensity": True}):
        with pytest.raises(ValueError):
            pacer.offer([bad])
    assert pacer.offer([item])


def test_interruption_and_excessive_lateness_fence_without_replay():
    pacer = NativeSpeechPacer()
    pacer.offer([hop(0), hop(1)])
    pacer.arm(stream_id="s", output_started_at=20.0)
    assert pacer.take_due(now=20.2) == []
    assert pacer.fenced
    assert not pacer.offer([hop(2)])
    assert pacer.take_due(now=20.21) == []
    with pytest.raises(ValueError):
        pacer.arm(stream_id="s", output_started_at=21.0)


def test_native_pacer_drops_old_hops_and_continues_with_fresh_features():
    pacer = NativeSpeechPacer(drop_late=True)
    for start in (0, 8):
        assert pacer.offer([hop(i) for i in range(start, start + 8)])
    pacer.arm(stream_id="s", output_started_at=100.0)
    result = pacer.take_due(now=100.42)
    assert [entry["sequence"] for entry in result] == [6, 7, 8]
    assert not pacer.fenced
    assert all(100.42 - (100.0 + entry["offset_ms"] / 1000) <= 0.15 for entry in result)
    assert [entry["sequence"] for entry in pacer.take_due(now=100.46)] == [9]


def test_unassociated_or_interrupted_stream_cannot_emit():
    pacer = NativeSpeechPacer()
    pacer.offer([hop(0)])
    assert pacer.take_due(now=10.0) == []
    with pytest.raises(ValueError):
        pacer.arm(stream_id="different", output_started_at=10.0)
    pacer.arm(stream_id="s", output_started_at=10.0)
    pacer.invalidate()
    assert pacer.take_due(now=10.0) == []
    assert not pacer.offer([hop(1)])


def test_malformed_batches_are_atomic_and_overflow_fails_closed():
    pacer = NativeSpeechPacer(max_hops=2)
    with pytest.raises(ValueError):
        pacer.offer([hop(0), {**hop(1), "pcm": [1]}])
    assert pacer.offer([hop(0), hop(1)])
    assert not pacer.offer([hop(2)])
    assert pacer.fenced
    assert pacer.take_due(now=0) == []


def test_mixed_and_out_of_order_streams_are_rejected_without_consumption():
    pacer = NativeSpeechPacer()
    with pytest.raises(ValueError):
        pacer.offer([hop(0), hop(1, stream="other")])
    with pytest.raises(ValueError):
        pacer.offer([hop(1)])
    assert pacer.offer([hop(0)])
    pacer.arm(stream_id="s", output_started_at=3.0)
    assert [entry["sequence"] for entry in pacer.take_due(now=3.0)] == [0]
