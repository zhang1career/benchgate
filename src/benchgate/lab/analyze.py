"""Analysis layer for stored lab data.

Two families of analysis, matching the two time axes:

* **Within one acquisition** — operate on a :class:`Waveform`'s sample axis:
  ``crop`` / ``resample_uniform`` / ``align_waveforms`` / ``overlay`` /
  ``compare_waveforms``.
* **Across acquisitions** — operate on a metric pulled by
  :meth:`LabDataStore.metric_series`: ``metric_stats`` / ``drift`` /
  ``compare_runs``.

Thin, composable functions over numpy — no DSL, no pandas dependency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

import numpy as np

from benchgate.instruments.types import Waveform


# --------------------------------------------------------------------------
# Within-acquisition: waveform operations
# --------------------------------------------------------------------------

def crop(wf: Waveform, t_start: float | None = None, t_end: float | None = None) -> Waveform:
    """Return a waveform restricted to ``[t_start, t_end]`` on its sample axis."""
    lo = -np.inf if t_start is None else t_start
    hi = np.inf if t_end is None else t_end
    mask = (wf.time_s >= lo) & (wf.time_s <= hi)
    raw = wf.raw_adc[mask] if wf.raw_adc is not None else None
    return Waveform(
        time_s=wf.time_s[mask],
        voltage_v=wf.voltage_v[mask],
        channel=wf.channel,
        timestamp=wf.timestamp,
        sample_rate_hz=wf.sample_rate_hz,
        raw_adc=raw,
        scaling=wf.scaling,
    )


def resample_uniform(wf: Waveform, *, num: int | None = None, dt: float | None = None) -> Waveform:
    """Resample onto a uniform time grid by linear interpolation.

    Provide either ``num`` (number of points) or ``dt`` (sample spacing).
    """
    if wf.time_s.size == 0:
        return wf
    t0, t1 = float(wf.time_s[0]), float(wf.time_s[-1])
    if dt is not None:
        grid = np.arange(t0, t1, dt)
    elif num is not None:
        grid = np.linspace(t0, t1, int(num))
    else:
        raise ValueError("resample_uniform requires either num or dt")
    v = np.interp(grid, wf.time_s, wf.voltage_v)
    sr = (1.0 / (grid[1] - grid[0])) if grid.size > 1 else wf.sample_rate_hz
    return Waveform(
        time_s=grid,
        voltage_v=v,
        channel=wf.channel,
        timestamp=wf.timestamp,
        sample_rate_hz=sr,
    )


def _common_grid(wfs: Sequence[Waveform], num: int | None) -> np.ndarray:
    starts = [float(w.time_s[0]) for w in wfs if w.time_s.size]
    ends = [float(w.time_s[-1]) for w in wfs if w.time_s.size]
    if not starts:
        return np.asarray([], dtype=float)
    lo, hi = max(starts), min(ends)  # overlapping window
    if hi <= lo:
        raise ValueError("waveforms do not share an overlapping time window")
    n = num or min(int(w.time_s.size) for w in wfs if w.time_s.size)
    return np.linspace(lo, hi, max(2, n))


def align_waveforms(wfs: Sequence[Waveform], *, num: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate several waveforms onto a shared (overlapping) time grid.

    Returns ``(t_grid, matrix)`` where ``matrix[i]`` is waveform ``i`` resampled.
    Waveforms are aligned on the trigger-relative time axis they already share.
    """
    grid = _common_grid(wfs, num)
    matrix = np.vstack([np.interp(grid, w.time_s, w.voltage_v) for w in wfs]) if grid.size else np.empty((0, 0))
    return grid, matrix


@dataclass
class Envelope:
    t_grid: np.ndarray
    mean: np.ndarray
    std: np.ndarray
    vmin: np.ndarray
    vmax: np.ndarray


def overlay(wfs: Sequence[Waveform], *, num: int | None = None) -> Envelope:
    """Align waveforms and compute a per-sample mean / std / min / max band."""
    grid, matrix = align_waveforms(wfs, num=num)
    if matrix.size == 0:
        empty = np.asarray([], dtype=float)
        return Envelope(grid, empty, empty, empty, empty)
    return Envelope(
        t_grid=grid,
        mean=matrix.mean(axis=0),
        std=matrix.std(axis=0),
        vmin=matrix.min(axis=0),
        vmax=matrix.max(axis=0),
    )


@dataclass
class WaveformComparison:
    n: int
    rmse: float
    max_abs_err: float
    correlation: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare_waveforms(a: Waveform, b: Waveform, *, num: int | None = None) -> WaveformComparison:
    """Compare two waveforms on their overlapping window (b interpolated onto a)."""
    grid, matrix = align_waveforms([a, b], num=num)
    if matrix.shape[1] == 0:
        return WaveformComparison(0, float("nan"), float("nan"), float("nan"))
    va, vb = matrix[0], matrix[1]
    diff = va - vb
    rmse = float(np.sqrt(np.mean(diff**2)))
    max_abs = float(np.max(np.abs(diff)))
    if np.std(va) == 0 or np.std(vb) == 0:
        corr = float("nan")
    else:
        corr = float(np.corrcoef(va, vb)[0, 1])
    return WaveformComparison(n=int(grid.size), rmse=rmse, max_abs_err=max_abs, correlation=corr)


# --------------------------------------------------------------------------
# Across-acquisitions: metric series operations
# --------------------------------------------------------------------------

def _rows_to_arrays(rows: Iterable[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    """Convert metric_series rows -> (t_seconds_since_first, values)."""
    items = list(rows)
    if not items:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    times: list[datetime] = [r["captured_at"] for r in items]
    t0 = min(times)
    secs = np.asarray([(t - t0).total_seconds() for t in times], dtype=float)
    vals = np.asarray([float(r["value"]) for r in items], dtype=float)
    order = np.argsort(secs)
    return secs[order], vals[order]


@dataclass
class MetricStats:
    n: int
    mean: float
    std: float
    vmin: float
    vmax: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def metric_stats(rows: Iterable[dict[str, Any]]) -> MetricStats:
    _, vals = _rows_to_arrays(rows)
    if vals.size == 0:
        return MetricStats(0, float("nan"), float("nan"), float("nan"), float("nan"))
    return MetricStats(
        n=int(vals.size),
        mean=float(vals.mean()),
        std=float(vals.std()),
        vmin=float(vals.min()),
        vmax=float(vals.max()),
    )


@dataclass
class DriftResult:
    n: int
    slope_per_s: float
    intercept: float
    mean: float
    std: float
    first: float
    last: float
    span_s: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def drift(rows: Iterable[dict[str, Any]]) -> DriftResult:
    """Linear trend of a metric over wall-clock time (slope per second)."""
    secs, vals = _rows_to_arrays(rows)
    n = int(vals.size)
    if n == 0:
        nan = float("nan")
        return DriftResult(0, nan, nan, nan, nan, nan, nan, 0.0)
    if n == 1 or np.ptp(secs) == 0:
        slope, intercept = 0.0, float(vals[0])
    else:
        slope, intercept = np.polyfit(secs, vals, 1)
    return DriftResult(
        n=n,
        slope_per_s=float(slope),
        intercept=float(intercept),
        mean=float(vals.mean()),
        std=float(vals.std()),
        first=float(vals[0]),
        last=float(vals[-1]),
        span_s=float(secs[-1] - secs[0]),
    )


@dataclass
class RunComparison:
    delta: float
    rel_error: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare_runs(a: float, b: float) -> RunComparison:
    """Compare two scalar metric values: ``delta = b - a``, relative to ``a``."""
    delta = float(b) - float(a)
    rel = delta / float(a) if a != 0 else float("nan")
    return RunComparison(delta=delta, rel_error=rel)
