"""Square-wave metrics with functional vs performance profiles.

Functional profile uses spike-tolerant steady levels (median split) for Vpp
and pass/fail. Performance profile adds peak/overshoot metrics from raw extrema
relative to those steady levels.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Literal

import numpy as np

MeasureProfile = Literal["functional", "performance"]


class Profile(str, Enum):
    FUNCTIONAL = "functional"
    PERFORMANCE = "performance"


@dataclass(frozen=True)
class SteadyLevels:
    vtop: float
    vbase: float
    vpp: float
    vmax: float
    vmin: float


@dataclass(frozen=True)
class PeakMetrics:
    overshoot_pos: float
    undershoot_neg: float
    vpp_raw: float
    overshoot_frac: float
    undershoot_frac: float


@dataclass(frozen=True)
class ScopeSnapshot:
    vtop: float | None
    vbase: float | None
    vpp: float | None
    vmax: float | None
    vmin: float | None
    freq: float | None = None
    duty: float | None = None


def rigol_voltage_from_bytes(
    raw: np.ndarray | list,
    *,
    yref: float,
    yinc: float,
    yorig: float,
) -> np.ndarray:
    """Rigol DS1000Z BYTE wave: V = (point - YREF) * YINC + YORIG."""
    samples = np.asarray(raw, dtype=float)
    return (samples - yref) * yinc + yorig


def steady_levels(voltage: np.ndarray | list) -> SteadyLevels:
    """Spike-tolerant steady VTOP/VBASE from level split (not raw max-min)."""
    v = np.asarray(voltage, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return SteadyLevels(0.0, 0.0, 0.0, 0.0, 0.0)
    vmax = float(v.max())
    vmin = float(v.min())
    med = float(np.median(v))
    upper = v[v >= med]
    lower = v[v < med]
    if upper.size >= 5 and lower.size >= 5:
        vtop = float(np.median(upper))
        vbase = float(np.median(lower))
    else:
        vtop = float(np.percentile(v, 90))
        vbase = float(np.percentile(v, 10))
    return SteadyLevels(vtop, vbase, vtop - vbase, vmax, vmin)


def peak_metrics(steady: SteadyLevels) -> PeakMetrics:
    """Non-ideal peaks relative to steady levels."""
    overshoot_pos = steady.vmax - steady.vtop
    undershoot_neg = steady.vbase - steady.vmin
    vpp_raw = steady.vmax - steady.vmin
    denom = steady.vpp if steady.vpp > 1e-9 else 1e-9
    return PeakMetrics(
        overshoot_pos=overshoot_pos,
        undershoot_neg=undershoot_neg,
        vpp_raw=vpp_raw,
        overshoot_frac=overshoot_pos / denom,
        undershoot_frac=undershoot_neg / denom,
    )


def parse_scope_snapshot(raw: dict[str, Any] | None) -> ScopeSnapshot | None:
    if not raw:
        return None
    return ScopeSnapshot(
        vtop=raw.get("vtop"),
        vbase=raw.get("vbase"),
        vpp=raw.get("vpp"),
        vmax=raw.get("vmax"),
        vmin=raw.get("vmin"),
        freq=raw.get("freq"),
        duty=raw.get("duty"),
    )


def analyze_waveform(
    voltage: np.ndarray | list,
    profile: MeasureProfile = "functional",
    *,
    scope: dict[str, Any] | ScopeSnapshot | None = None,
) -> dict[str, Any]:
    """Return steady levels; performance profile adds peak metrics."""
    steady = steady_levels(voltage)
    out: dict[str, Any] = {
        "profile": profile,
        "vtop": steady.vtop,
        "vbase": steady.vbase,
        "vpp": steady.vpp,
        "vmax": steady.vmax,
        "vmin": steady.vmin,
    }
    snap = scope if isinstance(scope, ScopeSnapshot) else parse_scope_snapshot(scope)
    if snap is not None:
        out["scope"] = {
            k: v
            for k, v in asdict(snap).items()
            if v is not None
        }
        if snap.vtop is not None:
            out["vtop_scope"] = snap.vtop
        if snap.vbase is not None:
            out["vbase_scope"] = snap.vbase
        if snap.vpp is not None:
            out["vpp_scope"] = snap.vpp
    if profile == "performance":
        peak = peak_metrics(steady)
        out.update(asdict(peak))
    return out


def correlation(a: np.ndarray | list, b: np.ndarray | list) -> float:
    """Pearson correlation of zero-mean waveforms."""
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    n = min(x.size, y.size)
    if n == 0:
        return 0.0
    x = x[:n] - x[:n].mean()
    y = y[:n] - y[:n].mean()
    d = float(np.linalg.norm(x) * np.linalg.norm(y))
    return float(np.dot(x, y) / d) if d > 0 else 0.0


def performance_pass(
    metrics: dict[str, Any],
    *,
    max_overshoot_frac: float | None,
    max_undershoot_frac: float | None,
) -> tuple[bool, list[str]]:
    """Gate peak metrics; no-op when limits are None."""
    reasons: list[str] = []
    if max_overshoot_frac is not None:
        val = metrics.get("overshoot_frac")
        if val is not None and val > max_overshoot_frac:
            reasons.append(f"overshoot_frac={val:.3f} > {max_overshoot_frac}")
    if max_undershoot_frac is not None:
        val = metrics.get("undershoot_frac")
        if val is not None and val > max_undershoot_frac:
            reasons.append(f"undershoot_frac={val:.3f} > {max_undershoot_frac}")
    return len(reasons) == 0, reasons
