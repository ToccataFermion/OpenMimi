"""Human-like pointer-trajectory generation for slider CAPTCHAs.

Extracted from ``ComputerTool`` so the same curves can be reused for CDP-level
``Input.dispatchMouseEvent`` drags without depending on OS mouse APIs.
"""
from __future__ import annotations

import math
import random


def generate_trajectory(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    steps: int = 80,
    delay_ms: int = 15,
    humanize: bool = True,
) -> list[tuple[int, int, int]]:
    """Return a list of (x, y, step_delay_ms) points for a drag sequence.

    When *humanize* is ``True`` the track follows a quadratic Bezier with
    ease-in-out cubic velocity, micro-jitter, occasional hesitations, and a
    small wiggle near the target.

    When *humanize* is ``False`` a straight linear interpolation is used.
    """
    if not humanize:
        pts: list[tuple[int, int, int]] = []
        for i in range(steps + 1):
            t = i / steps if steps else 1.0
            x = int(start_x + (end_x - start_x) * t)
            y = int(start_y + (end_y - start_y) * t)
            pts.append((x, y, delay_ms))
        pts[-1] = (end_x, end_y, delay_ms)
        return _dedupe(pts)

    dx = end_x - start_x
    dy = end_y - start_y
    distance = math.hypot(dx, dy)
    if distance < 1:
        return [(end_x, end_y, delay_ms)]

    # Control point offset perpendicular to the straight-line path
    offset_mag = min(80, distance * random.uniform(0.05, 0.15))
    side = 1 if random.random() < 0.5 else -1
    perp_x = -dy / distance * offset_mag * side
    perp_y = dx / distance * offset_mag * side
    ctrl_x = (start_x + end_x) / 2 + perp_x
    ctrl_y = (start_y + end_y) / 2 + perp_y

    # Adaptive step count
    steps = max(3, min(steps, int(distance / 3) + 3))

    track: list[tuple[int, int, int]] = []
    for i in range(steps + 1):
        t = i / steps
        t_eased = t * t * (3 - 2 * t)
        bx = int(
            (1 - t_eased) ** 2 * start_x
            + 2 * (1 - t_eased) * t_eased * ctrl_x
            + t_eased**2 * end_x
        )
        by = int(
            (1 - t_eased) ** 2 * start_y
            + 2 * (1 - t_eased) * t_eased * ctrl_y
            + t_eased**2 * end_y
        )
        jitter = random.randint(-1, 1)
        jx = int(bx + jitter * perp_x / offset_mag) if offset_mag > 0 else bx
        jy = int(by + jitter * perp_y / offset_mag) if offset_mag > 0 else by
        step_delay = int(delay_ms * random.uniform(0.7, 1.3))
        if random.random() < 0.05:
            step_delay += random.randint(20, 60)
        track.append((jx, jy, max(1, step_delay)))

    # Wiggle near target
    for _ in range(random.randint(0, 2)):
        wx = end_x + random.randint(-2, 2)
        wy = end_y + random.randint(-1, 1)
        track.append((wx, wy, int(random.uniform(50, 150))))

    track.append((end_x, end_y, delay_ms))
    return _dedupe(track)


def _dedupe(track: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """Remove consecutive duplicate points while preserving delay info."""
    if not track:
        return track
    out = [track[0]]
    for x, y, d in track[1:]:
        if (x, y) != (out[-1][0], out[-1][1]):
            out.append((x, y, d))
        else:
            # Same coordinate — keep the larger delay (more realistic pause)
            out[-1] = (x, y, max(out[-1][2], d))
    return out


__all__ = ["generate_trajectory"]
