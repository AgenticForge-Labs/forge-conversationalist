"""Layered head motion: a constant wobble with occasional glances on top.

Hardware-free. Proves the shape the operator asked for: a small base motion
that never stops, with short excursions layered over it that return to the
base rather than replacing it.
"""

from __future__ import annotations

import math

from forge_conversationalist.head_motion import GLANCES, HeadWobble


def test_base_motion_never_stops_and_stays_small():
    wobble = HeadWobble(seed=2, overlay_interval_s=(3600.0, 7200.0))
    offsets = [wobble.step(0.5, level=0.0) for _ in range(240)]
    assert all(abs(o.yaw) < 0.12 for o in offsets)
    assert all(abs(o.pitch) < 0.15 for o in offsets)
    # It keeps moving: consecutive hops differ.
    assert len({round(o.yaw, 5) for o in offsets}) > 100


def test_overlay_is_layered_then_returns_to_the_base():
    wobble = HeadWobble(seed=4, overlay_interval_s=(2.0, 2.0))
    steps = [wobble.step(0.25, level=0.0) for _ in range(48)]
    peaks = [max(abs(o.yaw), abs(o.pitch)) for o in steps]
    assert max(peaks) > 0.2, "an excursion should be visible"
    # Most hops are the small base motion; excursions are the exception.
    quiet = [p for p in peaks if p < 0.12]
    assert len(quiet) > len(peaks) * 0.6


def test_every_glance_is_small_and_short():
    for glance in GLANCES:
        assert abs(glance.yaw) <= 0.6 and abs(glance.pitch) <= 0.5
        assert glance.duration_s <= 2.0


def test_operator_can_trigger_a_glance_immediately():
    wobble = HeadWobble(seed=1, overlay_interval_s=(3600.0, 7200.0))
    wobble.step(0.1)
    wobble.overlay_now(GLANCES[0])
    declared = wobble.step(0.3, level=0.0)
    assert wobble.active_overlay == GLANCES[0].name
    assert math.copysign(1, declared.yaw) == math.copysign(1, GLANCES[0].yaw)


def test_speaking_raises_the_base_but_keeps_it_bounded():
    idle = HeadWobble(seed=6, overlay_interval_s=(3600.0, 7200.0))
    loud = HeadWobble(seed=6, overlay_interval_s=(3600.0, 7200.0))
    idle_peak = max(abs(idle.step(0.5, level=0.0).pitch) for _ in range(200))
    loud_peak = max(abs(loud.step(0.5, level=1.0).pitch) for _ in range(200))
    assert loud_peak > idle_peak
    assert loud_peak < 0.2


def test_two_instances_do_not_mirror_each_other():
    a = HeadWobble(seed=11)
    b = HeadWobble(seed=12)
    pairs = [(a.step(0.5).pitch, b.step(0.5).pitch) for _ in range(40)]
    assert any(abs(x - y) > 1e-3 for x, y in pairs)
