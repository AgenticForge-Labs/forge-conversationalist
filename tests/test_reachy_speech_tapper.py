"""Port of the pinned Reachy speech tapper, used to drive the daemon wobbler.

The stock Conversation App feeds synthesized PCM to ``SwayRollRT`` and sends
per-hop offsets over ``SetSpeechOffsetsCmd``; this repo does not reimplement
that scheduler, but it needs the same numbers to prove behaviour offline.

These tests are hardware-free: they check the port against the pinned
constants and prove that silence produces no motion while speech does.
"""

from __future__ import annotations

import math

import pytest

from forge_conversationalist.reachy_speech_tapper import (
    HOP_MS,
    SWAY_A_PITCH_DEG,
    SWAY_A_YAW_DEG,
    IdleDrift,
    SpeechTapper,
)
from forge_conversationalist.speech_envelope import SpeechEnvelopeAnalyzer

SAMPLE_RATE = 24000
HOP_SAMPLES = int(SAMPLE_RATE * HOP_MS / 1000)


def test_tapper_matches_pinned_sdk_reference_for_window_phase_and_release():
    """Golden values captured from reachy-mini 1.10.0rc5 SwayRollRT, seed 7."""
    tapper = SpeechTapper(sample_rate=SAMPLE_RATE)
    inputs = [
        [0.4] * 720 + [0.0] * 480,
        [0.25] * HOP_SAMPLES,
        [0.25] * HOP_SAMPLES,
        [0.0] * HOP_SAMPLES,
    ]
    expected = [
        (0, 0, 0, 0, 0, 0),
        (0.005604454297224592, 0.003867986579454588, -0.001671080125010955,
         -0.027372167711966128, -0.08103546606354095, -0.043390329170689655),
        (0.006257070809183545, 0.003912067068676745, -0.001684290982021629,
         -0.01015393204038672, -0.030871359069901044, -0.0148308439715694),
        (0, 0, 0, 0, 0, 0),
    ]
    for samples, reference in zip(inputs, expected, strict=True):
        assert tapper.feed(samples)[0] == pytest.approx(reference, abs=1e-12)


def tone(amplitude: float) -> list[float]:
    return [
        amplitude * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE)
        for i in range(HOP_SAMPLES)
    ]


def test_silence_produces_no_head_offset():
    tapper = SpeechTapper(sample_rate=SAMPLE_RATE)
    offsets = []
    for _ in range(10):
        offsets.extend(tapper.feed([0.0] * HOP_SAMPLES))
    assert offsets, "the tapper must emit one offset per hop"
    assert all(all(abs(value) < 1e-9 for value in hop) for hop in offsets)


def test_speech_produces_bounded_head_motion():
    tapper = SpeechTapper(sample_rate=SAMPLE_RATE)
    offsets = []
    for _ in range(20):
        offsets.extend(tapper.feed(tone(0.3)))
    rotations = [max(abs(o[3]), abs(o[4]), abs(o[5])) for o in offsets]
    assert max(rotations) > math.radians(1.0), "speech must move the head"
    # Never beyond the pinned per-axis amplitudes scaled by SWAY_MASTER.
    assert max(rotations) <= math.radians(max(SWAY_A_PITCH_DEG, SWAY_A_YAW_DEG) * 1.5)


def test_offsets_are_position_then_rotation_for_the_daemon():
    tapper = SpeechTapper(sample_rate=SAMPLE_RATE)
    offsets = []
    for _ in range(20):
        offsets.extend(tapper.feed(tone(0.3)))
    x_m, y_m, z_m, roll, pitch, yaw = offsets[-1]
    # Metres are small; rotations are radians.
    assert abs(x_m) < 0.02 and abs(y_m) < 0.02 and abs(z_m) < 0.02
    assert abs(roll) < 0.5 and abs(pitch) < 0.5 and abs(yaw) < 0.5


def test_idle_drift_stays_well_below_speech_amplitude():
    drift = IdleDrift()
    peaks = [max(abs(v) for v in drift.at(i * 0.1)[3:]) for i in range(200)]
    assert max(peaks) < math.radians(3.0)


def test_analyzer_output_is_what_the_tapper_consumes():
    """The existing envelope analyzer and this tapper share one hop size."""
    analyzer = SpeechEnvelopeAnalyzer(SAMPLE_RATE)
    pcm = b"".join(
        int(0.3 * 32767 * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE))
        .to_bytes(2, "little", signed=True)
        for i in range(HOP_SAMPLES)
    )
    hops = analyzer.feed_pcm16(pcm)
    assert hops, "one 50 ms frame should yield one hop"
    assert hops[0].duration_ms == HOP_MS
    assert hops[0].active is True
