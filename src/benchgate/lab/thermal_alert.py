"""Thermal anomaly alert: ΔT vs baseline (or vs frame median). Pure function, no I/O."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
from scipy import ndimage

from benchgate.instruments.types import Frame2D
from benchgate.lab.field2d import FrameGeometry, find_above_array


@dataclass(frozen=True)
class AlertPolicy:
    delta_warn: float | None = None
    delta_fail: float | None = None
    k_sigma_warn: float | None = None
    k_sigma_fail: float | None = None
    min_area_px: int = 2
    max_regions: int = 5
    require_baseline: bool = True


@dataclass(frozen=True)
class AlertRegion:
    peak_row: int
    peak_col: int
    peak_delta: float
    mean_delta: float
    area_px: int
    centroid_x: float
    centroid_y: float
    peak_x: float
    peak_y: float


@dataclass(frozen=True)
class AlertResult:
    severity: str
    regions: list[AlertRegion] = field(default_factory=list)
    unit: str = "count"
    t_ref: float = 0.0
    baseline_used: bool = False
    threshold_warn: float | None = None
    threshold_fail: float | None = None
    policy_source: str = "explicit"


def _resolve_thresholds(
    policy: AlertPolicy, sigma: np.ndarray | None
) -> tuple[float | None, float | None, str]:
    if policy.delta_warn is not None or policy.delta_fail is not None:
        return policy.delta_warn, policy.delta_fail, "explicit"
    if policy.k_sigma_warn is None and policy.k_sigma_fail is None:
        raise ValueError("alert policy needs delta_warn/delta_fail or k_sigma_warn/k_sigma_fail")
    if sigma is None:
        raise ValueError("k_sigma thresholds require a baseline sigma array")
    finite = np.asarray(sigma, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        raise ValueError("baseline sigma has no finite pixels")
    med = float(np.median(finite))
    warn = None if policy.k_sigma_warn is None else float(policy.k_sigma_warn) * med
    fail = None if policy.k_sigma_fail is None else float(policy.k_sigma_fail) * med
    return warn, fail, "k_sigma"


def _detect_threshold(warn: float | None, fail: float | None) -> float:
    vals = [v for v in (warn, fail) if v is not None]
    if not vals:
        raise ValueError("alert policy resolved no numeric threshold")
    return min(vals)


def evaluate_alert(
    frame: Frame2D,
    baseline: np.ndarray | None = None,
    sigma: np.ndarray | None = None,
    policy: AlertPolicy | None = None,
    *,
    baseline_unit: str | None = None,
    geometry: FrameGeometry | None = None,
) -> AlertResult:
    policy = policy or AlertPolicy()
    if baseline is None and policy.require_baseline:
        raise ValueError("evaluate_alert requires a baseline (or set require_baseline=False)")
    if baseline is not None and baseline_unit is not None and baseline_unit != frame.unit:
        raise ValueError(f"baseline unit {baseline_unit!r} != frame unit {frame.unit!r}")

    vals = np.asarray(frame.values, dtype=float).copy()
    if frame.mask is not None:
        vals[np.asarray(frame.mask, dtype=bool)] = np.nan
    if baseline is not None:
        base = np.asarray(baseline, dtype=float)
        if base.shape != vals.shape:
            raise ValueError(f"baseline shape {base.shape} != frame shape {vals.shape}")
        delta = vals - base
        finite_base = base[np.isfinite(base)]
        if finite_base.size == 0:
            raise ValueError("baseline has no finite pixels")
        t_ref = float(np.median(finite_base))
        baseline_used = True
    else:
        finite = vals[np.isfinite(vals)]
        if finite.size == 0:
            raise ValueError("no valid pixels in frame")
        t_ref = float(np.median(finite))
        delta = vals - t_ref
        baseline_used = False

    warn, fail, source = _resolve_thresholds(policy, sigma)
    detect = _detect_threshold(warn, fail)
    spots = find_above_array(
        delta,
        detect,
        geometry=geometry,
        min_area_px=int(policy.min_area_px),
        max_n=int(policy.max_regions),
        unit=frame.unit,
    )
    labeled, _ = ndimage.label(
        np.isfinite(delta) & (delta >= detect),
        structure=np.ones((3, 3), dtype=int),
    )
    regions: list[AlertRegion] = []
    for spot in spots:
        lab = int(labeled[spot.row, spot.col])
        region_vals = delta[labeled == lab] if lab > 0 else np.asarray([spot.value])
        regions.append(
            AlertRegion(
                peak_row=spot.row,
                peak_col=spot.col,
                peak_delta=float(spot.value),
                mean_delta=float(np.nanmean(region_vals)),
                area_px=spot.area_px,
                centroid_x=spot.centroid_x,
                centroid_y=spot.centroid_y,
                peak_x=spot.peak_x,
                peak_y=spot.peak_y,
            )
        )
    severity = "none"
    if regions:
        peak = max(r.peak_delta for r in regions)
        if fail is not None and peak >= fail:
            severity = "fail"
        elif warn is not None and peak >= warn:
            severity = "warn"
        elif fail is not None:
            severity = "fail"
        else:
            severity = "warn"
    return AlertResult(
        severity=severity,
        regions=regions,
        unit=frame.unit,
        t_ref=t_ref,
        baseline_used=baseline_used,
        threshold_warn=warn,
        threshold_fail=fail,
        policy_source=source,
    )


def alert_result_to_dict(result: AlertResult) -> dict:
    return {
        "severity": result.severity,
        "unit": result.unit,
        "t_ref": result.t_ref,
        "baseline_used": result.baseline_used,
        "threshold_warn": result.threshold_warn,
        "threshold_fail": result.threshold_fail,
        "policy_source": result.policy_source,
        "regions": [asdict(r) for r in result.regions],
    }
