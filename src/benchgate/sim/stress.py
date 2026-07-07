"""Component stress / derating checks from sim profile."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from benchgate.sim.analysis import (
    _compute_metric,
    _evaluate_bounds,
    _resolve_signal,
    _window_slice,
)
from benchgate.sim.expressions import eval_voltage_expression
from benchgate.sim.limits_catalog import load_limits_catalog, merge_stress_limits


@dataclass
class StressResult:
    reference: str
    quantity: str
    expr: str
    metric: str
    value: float
    limit: float
    derated_limit: float
    margin_pct: float
    passed: bool
    severity: str = "fail"  # pass | warn | fail
    message: str = ""
    worst_case: dict[str, str] | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if data.get("worst_case") is None:
            data.pop("worst_case", None)
        return data


@dataclass
class StressReport:
    passed: bool
    derating: float
    results: list[StressResult]
    warnings: list[str] | None = None

    def to_dict(self) -> dict:
        data = {
            "passed": self.passed,
            "derating": self.derating,
            "results": [r.to_dict() for r in self.results],
        }
        if self.warnings:
            data["warnings"] = self.warnings
        return data


def _probe_limit(probe: dict, limits: dict, derating: float) -> tuple[float, float]:
    if "lte" in probe:
        raw = float(probe["lte"])
        return raw, raw * derating
    key = probe.get("limit_key") or probe.get("limit")
    if key and key in limits:
        raw = float(limits[key])
        return raw, raw * derating
    raise KeyError("probe needs lte or limit_key referencing limits")


def _series_for_probe(
    probe: dict,
    signals: dict[str, np.ndarray],
    *,
    limits: dict[str, float],
) -> tuple[np.ndarray | None, str]:
    kind = str(probe.get("type", "voltage")).lower()
    use_abs = bool(probe.get("abs", False))

    if kind == "current":
        signal = str(probe.get("signal") or probe.get("expr") or "")
        series = _resolve_signal(signals, signal)
        label = signal
    elif kind == "power":
        vce_expr = str(probe.get("vce_expr") or probe.get("v_expr") or "")
        i_signal = str(probe.get("i_signal") or probe.get("i_expr") or "")
        vce = eval_voltage_expression(vce_expr, signals)
        ic = _resolve_signal(signals, i_signal)
        if vce is None or ic is None:
            return None, f"abs({vce_expr})*abs({i_signal})"
        series = np.abs(vce) * np.abs(ic)
        label = f"abs({vce_expr})*abs({i_signal})"
    elif kind == "tj":
        nested = dict(probe)
        nested["type"] = "power"
        pd, label = _series_for_probe(nested, signals, limits=limits)
        if pd is None:
            return None, label
        tamb = float(probe.get("tamb", 25.0))
        theta = float(probe.get("theta_ja", limits.get("theta_ja", 350.0)))
        series = tamb + pd * theta
        label = f"Tj({label}, theta={theta})"
    else:
        expr = str(probe.get("expr") or probe.get("signal") or "")
        series = eval_voltage_expression(f"abs({expr})" if use_abs else expr, signals)
        label = expr

    if series is not None and use_abs and kind not in ("power", "tj"):
        series = np.abs(series)
    return series, label


def _missing_severity(
    probe: dict,
    *,
    on_missing: str,
) -> str:
    if probe.get("required") is True:
        return "fail"
    if probe.get("required") is False:
        return "warn"
    return "fail" if on_missing == "fail" else "warn"


def evaluate_stress(
    time: np.ndarray,
    signals: dict[str, np.ndarray],
    stress_block: dict[str, Any],
    *,
    limits_catalog_path: Path | None = None,
) -> StressReport | None:
    if not stress_block:
        return None

    catalog = load_limits_catalog(limits_catalog_path)
    derating = float(stress_block.get("derating", 0.8))
    on_missing = str(stress_block.get("on_missing", "warn")).lower()
    fail_on_warn = bool(stress_block.get("fail_on_warn", False))
    components = stress_block.get("components") or {}
    results: list[StressResult] = []
    warnings: list[str] = []

    for ref, cfg in components.items():
        limits = merge_stress_limits(cfg, catalog)
        probes = cfg.get("probes") or {}
        window_after = cfg.get("window_after", stress_block.get("window_after"))

        for quantity, probe in probes.items():
            probe = dict(probe)
            metric = str(probe.get("metric", "max"))

            series, label = _series_for_probe(probe, signals, limits=limits)
            if series is None:
                sev = _missing_severity(probe, on_missing=on_missing)
                msg = "signal/expression could not be evaluated"
                if sev == "warn":
                    warnings.append(f"{ref}.{quantity}: {msg}")
                results.append(
                    StressResult(
                        reference=ref,
                        quantity=quantity,
                        expr=label,
                        metric=metric,
                        value=float("nan"),
                        limit=float("nan"),
                        derated_limit=float("nan"),
                        margin_pct=float("nan"),
                        passed=sev != "fail",
                        severity=sev,
                        message=msg,
                    )
                )
                continue

            try:
                raw_limit, derated = _probe_limit(probe, limits, derating)
            except KeyError:
                sev = _missing_severity(probe, on_missing=on_missing)
                msg = "missing limit / limit_key (check part catalog or limits)"
                if sev == "warn":
                    warnings.append(f"{ref}.{quantity}: {msg}")
                results.append(
                    StressResult(
                        reference=ref,
                        quantity=quantity,
                        expr=label,
                        metric=metric,
                        value=float("nan"),
                        limit=float("nan"),
                        derated_limit=float("nan"),
                        margin_pct=float("nan"),
                        passed=sev != "fail",
                        severity=sev,
                        message=msg,
                    )
                )
                continue

            mask = _window_slice(time, window_after)
            value = _compute_metric(series[mask], metric)
            passed, message = _evaluate_bounds(value, {"lte": derated})
            margin = (derated - value) / derated * 100.0 if derated > 0 else float("nan")
            if passed:
                sev = "pass"
            elif probe.get("required") is False:
                sev = "warn"
                passed = True
                if message:
                    message = f"{message} (informational probe)"
                warnings.append(f"{ref}.{quantity}: {message}")
            else:
                sev = "fail"

            results.append(
                StressResult(
                    reference=ref,
                    quantity=quantity,
                    expr=label,
                    metric=metric,
                    value=value,
                    limit=raw_limit,
                    derated_limit=derated,
                    margin_pct=margin,
                    passed=passed,
                    severity=sev,
                    message=message,
                )
            )

    all_passed = bool(results) and all(r.passed for r in results)
    if fail_on_warn and warnings:
        all_passed = False
    return StressReport(
        passed=all_passed,
        derating=derating,
        results=results,
        warnings=warnings or None,
    )


def merge_worst_stress(reports: list[StressReport]) -> StressReport:
    """Keep the highest stress value per (reference, quantity) across sweep points."""
    if not reports:
        return StressReport(passed=True, derating=0.8, results=[])

    derating = reports[0].derating
    worst: dict[tuple[str, str], StressResult] = {}

    for report in reports:
        for item in report.results:
            key = (item.reference, item.quantity)
            prev = worst.get(key)
            if prev is None or (not np.isnan(item.value) and item.value > prev.value):
                worst[key] = item

    results = list(worst.values())
    all_passed = bool(results) and all(r.passed for r in results)
    return StressReport(passed=all_passed, derating=derating, results=results)
