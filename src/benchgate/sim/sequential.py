"""Yield confidence intervals and sequential stopping for tolerance studies."""

from __future__ import annotations

import math


def wilson_yield_interval(
    passed: int,
    total: int,
    *,
    z: float = 1.96,
) -> tuple[float, float, float]:
    """Return (yield_pct, low_pct, high_pct) using Wilson score interval."""
    if total <= 0:
        return 0.0, 0.0, 0.0
    phat = passed / total
    denom = 1.0 + z**2 / total
    center = phat + z**2 / (2 * total)
    margin = z * math.sqrt((phat * (1 - phat) + z**2 / (4 * total)) / total)
    lo = max(0.0, (center - margin) / denom)
    hi = min(1.0, (center + margin) / denom)
    return 100.0 * phat, 100.0 * lo, 100.0 * hi


def sequential_should_stop(
    passed: int,
    total: int,
    *,
    min_samples: int,
    max_samples: int,
    ci_width_pct: float,
) -> tuple[bool, dict]:
    """Stop when Wilson CI width <= target or total >= max_samples."""
    yield_pct, lo, hi = wilson_yield_interval(passed, total)
    width = hi - lo
    info = {
        "yield_pct": yield_pct,
        "ci_low_pct": lo,
        "ci_high_pct": hi,
        "ci_width_pct": width,
        "n_samples": total,
    }
    if total >= max_samples:
        info["stop_reason"] = "max_samples"
        return True, info
    if total >= min_samples and width <= ci_width_pct:
        info["stop_reason"] = "ci_width"
        return True, info
    info["stop_reason"] = None
    return False, info
