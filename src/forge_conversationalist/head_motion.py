"""One layered head-motion composer shared by every robot.

The model is deliberately simple and robot-agnostic:

    base layer      a continuous, small wobble/breath that never stops
    overlay layer   occasional short excursions (look left/right, nod)
                    that leave the base running underneath and then return

Adapters turn the unitless result into device targets (Reachy metres and
radians for ``head_pose``; StackChan tenths of a degree for ``head_move``).
This module owns no device, no grants and no dispatch: the caller still
routes every target through the normal runtime, journal and STOP path.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class HeadOffset:
    """Unitless motion, -1..1 per axis, before device scaling."""

    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    lift: float = 0.0


@dataclass(frozen=True)
class Overlay:
    """A short excursion layered on top of the base motion."""

    name: str
    yaw: float
    pitch: float
    duration_s: float


# Excursions are small on purpose: a glance, not a turn.
GLANCES: tuple[Overlay, ...] = (
    Overlay("look_left", -0.55, 0.05, 1.8),
    Overlay("look_right", 0.55, 0.05, 1.8),
    Overlay("look_up", 0.08, -0.45, 1.4),
    Overlay("look_down", -0.05, 0.40, 1.4),
    Overlay("nod", 0.0, 0.35, 1.0),
)


class HeadWobble:
    """Base wobble plus occasional overlays, deterministic per seed.

    ``level`` is the caller's urgency: 0 while idle, up to 1 while speaking
    or reacting to loud audio. It scales the base amplitude only; overlays are
    independent so speech never suppresses a glance entirely.
    """

    def __init__(
        self,
        *,
        seed: int | None = None,
        overlay_interval_s: tuple[float, float] = (6.0, 12.0),
        speaking_interval_s: tuple[float, float] = (1.5, 2.5),
    ) -> None:
        self._rng = random.Random(seed)
        tau = 2 * math.pi
        # Per-instance phases: two robots in one room must not mirror each other.
        self._phase = [self._rng.random() * tau for _ in range(4)]
        self._overlay_interval_s = overlay_interval_s
        self._speaking_interval_s = speaking_interval_s
        self._t = 0.0
        self._active: Overlay | None = None
        self._active_until = 0.0
        self._next_overlay = self._rng.uniform(*overlay_interval_s)

    @property
    def active_overlay(self) -> str | None:
        return self._active.name if self._active else None

    def _base(self, t: float, level: float) -> HeadOffset:
        """Slow breath plus a small sway; never zero, never large."""
        tau = 2 * math.pi
        p0, p1, p2, p3 = self._phase
        breath = math.sin(tau * t / 7.5 + p0)
        sway = math.sin(tau * t / 11.0 + p1)
        drift = math.sin(tau * t / 5.3 + p2)
        lift = math.sin(tau * t / 9.0 + p3)
        scale = 0.35 + 0.65 * min(1.0, max(0.0, level))
        return HeadOffset(
            yaw=scale * (0.05 * sway + 0.02 * drift),
            pitch=scale * (0.06 * breath + 0.02 * drift),
            roll=scale * 0.025 * drift,
            lift=scale * 0.03 * lift,
        )

    def step(self, dt: float, *, level: float = 0.0) -> HeadOffset:
        """Advance time and return the composed offset for this hop."""
        self._t += dt
        base = self._base(self._t, level)
        if self._active is not None:
            if self._t >= self._active_until:
                self._active = None
            else:
                # Ease in and out so the excursion never snaps.
                span = max(1e-6, self._active_until - (self._active_until - self._active.duration_s))
                elapsed = self._t - (self._active_until - self._active.duration_s)
                phase = min(1.0, max(0.0, elapsed / span))
                weight = math.sin(math.pi * phase) if span else 1.0
                return HeadOffset(
                    yaw=base.yaw + self._active.yaw * weight,
                    pitch=base.pitch + self._active.pitch * weight,
                    roll=base.roll,
                    lift=base.lift,
                )
        if self._t >= self._next_overlay:
            interval = (
                self._speaking_interval_s if level > 0.5 else self._overlay_interval_s
            )
            self._active = self._rng.choice(GLANCES)
            self._active_until = self._t + self._active.duration_s
            self._next_overlay = self._active_until + self._rng.uniform(*interval)
        return base

    def overlay_now(self, overlay: Overlay) -> None:
        """Start a named excursion immediately (operator or gesture driven)."""
        self._active = overlay
        self._active_until = self._t + overlay.duration_s
