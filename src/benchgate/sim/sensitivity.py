"""Sensitivity analysis for tolerance / Monte Carlo studies."""

from __future__ import annotations

import numpy as np


def _rankdata(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=float)
    ranks[order] = np.arange(values.size, dtype=float)
    # Average ranks for ties
    sorted_vals = values[order]
    i = 0
    while i < sorted_vals.size:
        j = i
        while j + 1 < sorted_vals.size and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        if j > i:
            avg = 0.5 * (i + j)
            ranks[order[i : j + 1]] = avg
        i = j + 1
    return ranks


def spearman_correlation(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation; returns NaN when undefined."""
    if x.size < 3 or y.size < 3:
        return float("nan")
    if np.allclose(x, x[0]) or np.allclose(y, y[0]):
        return float("nan")
    rx = _rankdata(x)
    ry = _rankdata(y)
    return float(np.corrcoef(rx, ry)[0, 1])


def compute_sensitivity(
    points: list[dict],
    *,
    axis_refs: list[str],
    metric_keys: list[str],
) -> dict[str, dict[str, float | None]]:
    """Per-metric Spearman rho of each tolerance axis vs achieved metric."""
    out: dict[str, dict[str, float | None]] = {}
    for metric in metric_keys:
        y = np.asarray([p.get("metrics", {}).get(metric, float("nan")) for p in points], dtype=float)
        per_ref: dict[str, float | None] = {}
        for ref in axis_refs:
            x = np.asarray([p.get("u_norm", {}).get(ref, float("nan")) for p in points], dtype=float)
            mask = np.isfinite(x) & np.isfinite(y)
            if mask.sum() < 3:
                per_ref[ref] = None
                continue
            rho = spearman_correlation(x[mask], y[mask])
            per_ref[ref] = None if np.isnan(rho) else float(rho)
        out[metric] = per_ref
    return out


def top_sensitivity_drivers(
    sensitivity: dict[str, dict[str, float | None]],
    *,
    limit: int = 3,
) -> dict[str, list[dict[str, float | str | None]]]:
    """Rank refs by |rho| per metric."""
    drivers: dict[str, list[dict[str, float | str | None]]] = {}
    for metric, per_ref in sensitivity.items():
        ranked = []
        for ref, rho in per_ref.items():
            if rho is None:
                continue
            ranked.append({"ref": ref, "rho": rho, "abs_rho": abs(rho)})
        ranked.sort(key=lambda item: item["abs_rho"], reverse=True)
        drivers[metric] = ranked[:limit]
    return drivers
