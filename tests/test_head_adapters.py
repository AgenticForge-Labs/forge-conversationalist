"""The two motion modes, at device scale, for both robots.

Hardware-free. ``breath`` is pitch-only up/down with occasional layered
glances; ``talking`` is Reachy's speech-tapper wobble and StackChan's
firmware nod/drift. Nothing here dispatches.
"""

from __future__ import annotations

import math

import pytest

from forge_conversationalist.head_adapters import (
    REACHY_PITCH_AMPLITUDE_RAD,
    REACHY_YAW_AMPLITUDE_RAD,
    STACKCHAN_CENTER_PITCH,
    STACKCHAN_PITCH_AMPLITUDE,
    STACKCHAN_YAW_AMPLITUDE,
    Breath,
    pose_angles,
    reachy_breath_pose,
    reachy_talk_pose,
    stackchan_head,
)
from forge_conversationalist.reachy_speech_tapper import HOP_MS, SpeechTapper

NEUTRAL = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0],
           [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def test_breath_is_pitch_only():
    """Idle is breathing: up/down, never a sideways turn."""
    breath = Breath()
    for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.75, 3.5):
        yaw, _pitch, roll = pose_angles(reachy_breath_pose(NEUTRAL, breath, t))
        assert abs(yaw) < 1e-9, t
        assert abs(roll) < 1e-9, t
    # And it actually moves up and down over a cycle.
    pitches = [pose_angles(reachy_breath_pose(NEUTRAL, breath, t))[1]
               for t in (1.0, 3.0)]
    assert pitches[0] > 1.0 > pitches[1]
    assert abs(pitches[0]) <= math.degrees(REACHY_PITCH_AMPLITUDE_RAD) + 1e-6


def test_glance_is_layered_on_top_of_breathing():
    breath = Breath()
    calm = pose_angles(reachy_breath_pose(NEUTRAL, breath, 1.0))
    glance = pose_angles(reachy_breath_pose(NEUTRAL, breath, 1.0, yaw=-0.55))
    assert abs(glance[0]) > 2.0            # it looks sideways
    assert abs(glance[0]) <= math.degrees(REACHY_YAW_AMPLITUDE_RAD) + 1e-6
    # Breathing keeps running underneath the glance (same sign and scale).
    assert glance[1] * calm[1] > 0
    assert abs(glance[1] - calm[1]) < 0.05


def test_talking_hop_applies_each_rotation_on_its_own_axis():
    """One axis at a time, so a crossed mapping cannot hide in a combined pose."""
    for index, axis_name in ((3, "roll"), (4, "pitch"), (5, "yaw")):
        offset = [0.0] * 6
        offset[index] = math.radians(2.0)
        yaw, pitch, roll = pose_angles(reachy_talk_pose(NEUTRAL, tuple(offset)))
        readback = {"yaw": yaw, "pitch": pitch, "roll": roll}
        assert readback[axis_name] == pytest.approx(2.0, abs=0.01), axis_name
        others = [abs(v) for k, v in readback.items() if k != axis_name]
        assert max(others) < 0.01, (axis_name, readback)


def test_stackchan_breath_moves_only_pitch():
    centred = stackchan_head(breath=0.0)
    up = stackchan_head(breath=1.0)
    down = stackchan_head(breath=-1.0)
    assert centred["yaw"]["angle"] == 0
    assert up["yaw"]["angle"] == 0 == down["yaw"]["angle"]
    assert up["pitch"]["angle"] == STACKCHAN_CENTER_PITCH + STACKCHAN_PITCH_AMPLITUDE
    assert down["pitch"]["angle"] == STACKCHAN_CENTER_PITCH - STACKCHAN_PITCH_AMPLITUDE


def test_stackchan_glance_turns_without_leaving_bounds():
    left = stackchan_head(yaw=-0.55)["yaw"]["angle"]
    right = stackchan_head(yaw=0.55)["yaw"]["angle"]
    assert left == -round(0.55 * STACKCHAN_YAW_AMPLITUDE)
    assert right == round(0.55 * STACKCHAN_YAW_AMPLITUDE)
    extreme = stackchan_head(yaw=-40.0, breath=40.0)
    assert -1280 <= extreme["yaw"]["angle"] <= 1280
    assert 30 <= extreme["pitch"]["angle"] <= 870


def test_reachy_talking_matches_the_pinned_tapper_amplitude():
    tapper = SpeechTapper(sample_rate=24000)
    hop = int(24000 * HOP_MS / 1000)
    tone = [0.3 * math.sin(2 * math.pi * 220 * i / 24000) for i in range(hop)]
    offsets = []
    for _ in range(20):
        offsets.extend(tapper.feed(tone))
    biggest = max(offsets, key=lambda o: abs(o[5]))
    _yaw, _pitch, _roll = pose_angles(reachy_talk_pose(NEUTRAL, biggest))
    assert 1.0 < abs(_yaw) < 20.0
