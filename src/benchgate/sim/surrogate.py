"""Linear surrogate models for tolerance Monte Carlo studies."""

from __future__ import annotations

import numpy as np


def _feature_matrix(
    points: list[dict],
    *,
    dim_keys: list[str],
) -> tuple[np.ndarray, list[str]]:
    """Build design matrix from per-dimension normalized positions."""
    rows: list[list[float]] = []
    for point in points:
        u_dim = point.get("u_dim") or {}
        rows.append([float(u_dim.get(key, float("nan"))) for key in dim_keys])
    return np.asarray(rows, dtype=float), dim_keys


def fit_linear_surrogate(
    points: list[dict],
    *,
    dim_keys: list[str],
    metric_key: str,
) -> dict[str, float | list[float] | None] | None:
    """OLS surrogate: metric ≈ intercept + Σ coef_i * u_dim_i."""
    if len(points) < len(dim_keys) + 2:
        return None
    x, keys = _feature_matrix(points, dim_keys=dim_keys)
    y = np.asarray([p.get("metrics", {}).get(metric_key, float("nan")) for p in points], dtype=float)
    mask = np.all(np.isfinite(x), axis=1) & np.isfinite(y)
    if mask.sum() < len(dim_keys) + 2:
        return None
    x_fit = x[mask]
    y_fit = y[mask]
    design = np.column_stack([np.ones(x_fit.shape[0]), x_fit])
    coeffs, residuals, rank, _ = np.linalg.lstsq(design, y_fit, rcond=None)
    if rank < design.shape[1]:
        return None
    y_hat = design @ coeffs
    ss_res = float(np.sum((y_fit - y_hat) ** 2))
    ss_tot = float(np.sum((y_fit - np.mean(y_fit)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {
        "metric": metric_key,
        "r2": float(r2),
        "intercept": float(coeffs[0]),
        "coefficients": {keys[i]: float(coeffs[i + 1]) for i in range(len(keys))},
        "n_fit": int(mask.sum()),
    }


def _metric_passes(value: float, check: dict) -> bool:
    if not np.isfinite(value):
        return False
    if check.get("gte") is not None and value < float(check["gte"]):
        return False
    if check.get("lte") is not None and value > float(check["lte"]):
        return False
    return True


def fit_polynomial_surrogate(
    points: list[dict],
    *,
    dim_keys: list[str],
    metric_key: str,
    degree: int = 2,
) -> dict[str, float | list[float] | None] | None:
    """OLS with linear + quadratic terms per dimension."""
    if degree < 2:
        return fit_linear_surrogate(points, dim_keys=dim_keys, metric_key=metric_key)
    if len(points) < 2 * len(dim_keys) + 3:
        return fit_linear_surrogate(points, dim_keys=dim_keys, metric_key=metric_key)

    feature_names: list[str] = []
    rows: list[list[float]] = []
    for point in points:
        u_dim = point.get("u_dim") or {}
        feats: list[float] = []
        names: list[str] = []
        for key in dim_keys:
            u = float(u_dim.get(key, float("nan")))
            feats.extend([u, u * u])
            names.extend([key, f"{key}^2"])
        if not rows:
            feature_names = names
        rows.append(feats)

    x = np.asarray(rows, dtype=float)
    y = np.asarray([p.get("metrics", {}).get(metric_key, float("nan")) for p in points], dtype=float)
    mask = np.all(np.isfinite(x), axis=1) & np.isfinite(y)
    if mask.sum() < x.shape[1] + 2:
        return fit_linear_surrogate(points, dim_keys=dim_keys, metric_key=metric_key)
    x_fit = x[mask]
    y_fit = y[mask]
    design = np.column_stack([np.ones(x_fit.shape[0]), x_fit])
    coeffs, _, rank, _ = np.linalg.lstsq(design, y_fit, rcond=None)
    if rank < design.shape[1]:
        return fit_linear_surrogate(points, dim_keys=dim_keys, metric_key=metric_key)
    y_hat = design @ coeffs
    ss_res = float(np.sum((y_fit - y_hat) ** 2))
    ss_tot = float(np.sum((y_fit - np.mean(y_fit)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {
        "metric": metric_key,
        "degree": degree,
        "r2": float(r2),
        "intercept": float(coeffs[0]),
        "coefficients": {feature_names[i]: float(coeffs[i + 1]) for i in range(len(feature_names))},
        "n_fit": int(mask.sum()),
    }


def predict_yield_from_surrogates(
    surrogates: dict[str, dict],
    *,
    checks: list[dict],
    dim_keys: list[str],
    n_probe: int = 5000,
    seed: int = 0,
) -> float | None:
    """Estimate yield by probing random unit hypercube points through linear surrogates."""
    metric_keys = [s["metric"] for s in surrogates.values() if s]
    if not metric_keys or not dim_keys:
        return None
    rng = np.random.default_rng(seed)
    u = rng.random((n_probe, len(dim_keys)))
    passed = 0
    for i in range(n_probe):
        u_map = {dim_keys[j]: float(u[i, j]) for j in range(len(dim_keys))}
        ok = True
        for check in checks:
            key = check.get("alias") or f"{check.get('signal')}:{check.get('metric')}"
            model = surrogates.get(key)
            if not model:
                ok = False
                break
            pred = float(model["intercept"])
            for name, coef in model["coefficients"].items():
                if name.endswith("^2"):
                    base = name[:-2]
                    pred += float(coef) * (u_map.get(base, 0.0) ** 2)
                elif name in u_map:
                    pred += float(coef) * u_map[name]
            if not _metric_passes(pred, check):
                ok = False
                break
        if ok:
            passed += 1
    return 100.0 * passed / n_probe if n_probe else 0.0
