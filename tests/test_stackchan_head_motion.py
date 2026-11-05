"""StackChan head behaviour parity with its own firmware modifiers.

Hardware-free: these prove the ported ranges and weighting, not physical
motion. The provider still validates bounds and owns dispatch.
"""

from __future__ import annotations

from forge_conversationalist.stackchan_head_motion import (
    IDLE_INTERVAL_MAX_MS,
    IDLE_INTERVAL_MIN_MS,
    MOUTH_OPEN_MAX,
    MOUTH_OPEN_MIN,
    NOD_MAX,
    NOD_MIN,
    PITCH_DRIFT,
    SPEAKING_MOTION_MAX_MS,
    SPEAKING_MOTION_MIN_MS,
    YAW_DRIFT,
    StackChanHeadMotion,
)


def test_idle_targets_stay_inside_the_firmware_envelope():
    motion = StackChanHeadMotion(seed=3)
    for _ in range(500):
        target = motion.next_idle()
        assert -800 <= target.yaw <= 800
        assert 0 <= target.pitch <= 600
        assert 100 <= target.speed <= 400


def test_idle_looks_around_both_ways_and_keeps_moving():
    motion = StackChanHeadMotion(seed=7)
    targets = [motion.next_idle() for _ in range(60)]
    assert any(t.yaw > 50 for t in targets)
    assert any(t.yaw < -50 for t in targets)
    assert len({(t.yaw, t.pitch) for t in targets}) > 30


def test_speaking_motion_is_subtle_and_centred_on_the_current_pose():
    motion = StackChanHeadMotion(seed=5)
    motion.observe(120, 480)
    base_yaw, base_pitch = motion.current
    for _ in range(200):
        target = motion.next_speaking()
        # Nods and sways around the observed pose, never a large jump.
        assert abs(target.yaw - base_yaw) <= YAW_DRIFT * 2
        assert NOD_MIN - PITCH_DRIFT <= target.pitch - base_pitch <= NOD_MAX + PITCH_DRIFT
        assert 100 <= target.speed <= 200


def test_large_external_move_resets_the_baseline():
    motion = StackChanHeadMotion(seed=1)
    motion.observe(0, 450)
    motion.observe(600, 450)  # beyond the firmware's 300 threshold
    target = motion.next_speaking()
    assert abs(target.yaw - 600) <= YAW_DRIFT * 2


def test_mouth_weights_match_the_firmware_ranges():
    motion = StackChanHeadMotion(seed=2)
    opens = [motion.mouth_weight(opening=True) for _ in range(50)]
    closes = [motion.mouth_weight(opening=False) for _ in range(50)]
    assert all(c >= MOUTH_OPEN_MIN for c in opens)
    assert all(c <= MOUTH_OPEN_MAX for c in opens)
    assert all(c <= 20 for c in closes)


def test_intervals_match_the_firmware():
    assert (IDLE_INTERVAL_MIN_MS, IDLE_INTERVAL_MAX_MS) == (4000, 8000)
    assert (SPEAKING_MOTION_MIN_MS, SPEAKING_MOTION_MAX_MS) == (1500, 2500)
