"""Device adapters for the layered head motion.

Two modes, deliberately separate as the operator specified:

``breath``  pitch-only up/down, deterministic, with occasional look-around
            excursions layered on top (the app's idle is just breathing).
``talking`` Reachy uses the pinned speech tapper; StackChan uses the subtle
            nod/drift its own firmware performs while speaking.

This module only converts unitless motion into validated device targets.
Every caller still dispatches through the normal runtime, journal and STOP.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

REACHY_PITCH_AMPLITUDE_RAD = 0.035
REACHY_YAW_AMPLITUDE_RAD = 0.10
REACHY_TALK_AMPLITUDE_RAD = 0.09
STACKCHAN_PITCH_AMPLITUDE = 45
STACKCHAN_YAW_AMPLITUDE = 80
STACKCHAN_CENTER_PITCH = 450


def _axis(angle: float, axis: int) -> list[list[float]]:
    """Rotation about one axis of the head frame.

    The Reachy daemon reads a head matrix with ``as_euler("xyz")``, so the
    readback order is (roll, pitch, yaw): roll about x, pitch about y and yaw
    about z. Any other order puts the motion on the wrong axis.
    """
    c, s = math.cos(angle), math.sin(angle)
    if axis == 0:
        return [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]
    if axis == 1:
        return [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def compose_pose(
    base: list[list[float]],
    *,
    pitch_rad: float = 0.0,
    yaw_rad: float = 0.0,
    roll_rad: float = 0.0,
    vertical_m: float = 0.0,
) -> list[list[float]]:
    """Small orientation offset around the operator's reviewed neutral pose.

    Same convention as Puppeteer's composer: this only builds a transform.
    The Reachy SDK still validates limits and owns execution and STOP.
    """
    local = _axis(roll_rad, 0)
    for angle, axis in ((pitch_rad, 1), (yaw_rad, 2)):
        rotation = _axis(angle, axis)
        local = [
            [sum(local[i][k] * rotation[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)
        ]
    out = [
        [sum(base[i][k] * local[k][j] for k in range(3)) for j in range(3)]
        + [float(base[i][3]) + (vertical_m if i == 2 else 0.0)]
        for i in range(3)
    ]
    return out + [[0.0, 0.0, 0.0, 1.0]]


@dataclass(frozen=True)
class Breath:
    """Pitch-only breathing with occasional layered glances."""

    period_s: float = 4.0
    glance_interval_s: tuple[float, float] = (8.0, 16.0)

    def pitch_at(self, t: float) -> float:
        """-1..1 upward/downward breath; a single slow oscillator."""
        return math.sin(2 * math.pi * t / self.period_s)


def pose_angles(pose: list[list[float]]) -> tuple[float, float, float]:
    """Read back (yaw, pitch, roll) in degrees from a 4x4 head transform.

    Uses the daemon's own convention, ``R.from_matrix(...).as_euler("xyz")``,
    so this reports what Reachy would report for the same matrix. Kept free of
    scipy so the worker's dependency set does not grow for a readback helper.
    """
    # For R = Rz(yaw) Ry(pitch) Rx(roll): R[2][0] = -sin(pitch) and
    # R[0][2] = cos(pitch) sin(yaw).
    m = [[float(pose[i][j]) for j in range(3)] for i in range(3)]
    sin_pitch = max(-1.0, min(1.0, -m[2][0]))
    pitch = math.asin(sin_pitch)
    cos_pitch = math.cos(pitch)
    if abs(cos_pitch) > 1e-9:
        yaw = math.atan2(m[1][0], m[0][0])
        roll = math.atan2(m[2][1], m[2][2])
    else:  # gimbal lock; the remaining axes are degenerate
        yaw = 0.0
        roll = math.atan2(-m[0][1], m[1][1])
    return (math.degrees(yaw), math.degrees(pitch), math.degrees(roll))


def reachy_breath_pose(
    neutral: list[list[float]],
    breath: Breath,
    t: float,
    *,
    yaw: float = 0.0,
    look_pitch: float = 0.0,
) -> list[list[float]]:
    """Reachy ``head_pose`` matrix for breathing plus an optional glance.

    The pose is composed around the operator's reviewed neutral transform, so
    a glance never invents a new home pose.
    """
    pitch_rad = breath.pitch_at(t) * REACHY_PITCH_AMPLITUDE_RAD
    return compose_pose(
        neutral,
        pitch_rad=pitch_rad + look_pitch * REACHY_PITCH_AMPLITUDE_RAD,
        yaw_rad=yaw * REACHY_YAW_AMPLITUDE_RAD,
    )


def reachy_talk_pose(
    neutral: list[list[float]],
    offset: tuple[float, float, float, float, float, float],
    *,
    scale: float = 1.0,
) -> list[list[float]]:
    """Apply one speech-tapper hop to the neutral transform.

    ``offset`` is the daemon order (x, y, z, roll, pitch, yaw) in metres and
    radians; only rotation is applied here so a hop can never move the head
    beyond the operator's translation bound.
    """
    _x, _y, _z, roll, pitch, yaw = offset
    return compose_pose(
        neutral,
        pitch_rad=pitch * scale,
        yaw_rad=yaw * scale,
        roll_rad=roll * scale,
    )


def _tenths(value: float) -> int:
    """Round to the firmware's integer tenths-of-a-degree unit."""
    rounded = round(value)
    return rounded if isinstance(rounded, int) else int(rounded)


def stackchan_head(
    *,
    breath: float = 0.0,
    yaw: float = 0.0,
    pitch: float = 0.0,
    speed: int = 180,
) -> dict[str, dict[str, int]]:
    """StackChan ``head_move`` parameters in the firmware's tenths of a degree.

    ``breath`` is -1..1 (pitch only); ``yaw``/``pitch`` are the layered
    excursion as -1..1 fractions of the device amplitude.
    """
    yaw_t = _tenths(yaw * STACKCHAN_YAW_AMPLITUDE)
    pitch_t = _tenths(
        STACKCHAN_CENTER_PITCH + breath * STACKCHAN_PITCH_AMPLITUDE
        + pitch * STACKCHAN_PITCH_AMPLITUDE
    )
    return {
        "yaw": {"angle": max(-1280, min(1280, yaw_t)), "speed": speed},
        "pitch": {"angle": max(30, min(870, pitch_t)), "speed": speed},
    }
