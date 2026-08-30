"""Evaluate rule packs against simulation / MC evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from numbers import Real
from typing import Any

from benchgate.rules.loader import RuleDef, RulePack


@dataclass
class RuleContext:
    sim_report: dict[str, Any] | None = None
    monte_carlo: dict[str, Any] | None = None
    operating_point: dict[str, Any] | None = None
    gate_report: dict[str, Any] | None = None
    block_sweep_report: dict[str, Any] | None = None


@dataclass
class RuleResult:
    pack_id: str
    rule_id: str
    severity: str
    passed: bool
    message: str
    evidence: str
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RuleEvaluation:
    packs: list[str]
    passed: bool
    results: list[RuleResult] = field(default_factory=list)
    failures: int = 0
    warnings: int = 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "packs": self.packs,
            "failures": self.failures,
            "warnings": self.warnings,
            "results": [r.to_dict() for r in self.results],
        }


def _check_entry_matches(when: dict[str, Any], ctx: RuleContext) -> bool:
    if not when:
        return True
    op = ctx.operating_point or {}
    for key, expected in when.items():
        if op.get(key) != expected:
            return False
    return True


def _gate_waveform_rows(gate_report: dict | None) -> list[dict[str, Any]]:
    if not gate_report:
        return []
    rows: list[dict[str, Any]] = []
    for entry in gate_report.get("entries") or []:
        if entry.get("waveform_comparison") or entry.get("rmse") is not None:
            rows.append(
                {
                    "id": entry.get("reference"),
                    "rmse": entry.get("rmse"),
                    "waveform": entry.get("waveform_comparison"),
                    "waveform_status": entry.get("waveform_status"),
                }
            )
    for comp in gate_report.get("comparisons") or []:
        rows.append(comp)
    return rows


def _find_check_value(sim_report: dict | None, signal: str, metric: str) -> float | None:
    if not sim_report:
        return None
    checks = (sim_report.get("checks") or {}).get("checks") or []
    for item in checks:
        if item.get("signal") == signal and item.get("metric") == metric:
            val = item.get("value")
            if isinstance(val, Real):
                return float(val)
    return None


def _sweep_metric_values(ctx: RuleContext, metric: str) -> list[float]:
    report = ctx.block_sweep_report
    if not report:
        return []
    points = report.get("points") or []
    vals: list[float] = []
    for pt in points:
        m = pt.get("metrics") or {}
        val = m.get(metric)
        if val is None and metric == report.get("metric"):
            val = pt.get("metric")
        if isinstance(val, Real):
            vals.append(float(val))
    return vals


def _evaluate_limit(rule: RuleDef, ctx: RuleContext) -> tuple[bool, str]:
    limit = rule.limit
    ltype = str(limit.get("type") or "")

    if ltype == "check_metric":
        val = _find_check_value(ctx.sim_report, str(limit["signal"]), str(limit["metric"]))
        if val is None:
            return False, f"metric {limit['signal']}:{limit['metric']} not found in sim_report.checks"
        lo = limit.get("min")
        hi = limit.get("max")
        if isinstance(lo, Real) and val < float(lo):
            return False, f"value {val:g} below min {float(lo):g}"
        if isinstance(hi, Real) and val > float(hi):
            return False, f"value {val:g} above max {float(hi):g}"
        return True, f"value {val:g} within bounds"

    if ltype == "stress_passed":
        stress = (ctx.sim_report or {}).get("stress") or {}
        if not stress:
            return False, "no stress block in sim_report"
        allow_warn = bool(limit.get("allow_warn", True))
        if not stress.get("passed"):
            return False, "stress.passed is false"
        if not allow_warn and stress.get("warnings"):
            return False, f"stress warnings present: {stress.get('warnings')}"
        return True, "stress passed"

    if ltype == "stress_fail_count":
        stress = (ctx.sim_report or {}).get("stress") or {}
        results = stress.get("results") or []
        max_allowed = int(limit.get("max", 0))
        fails = [r for r in results if not r.get("passed") or r.get("severity") == "fail"]
        if len(fails) > max_allowed:
            return False, f"{len(fails)} stress failure(s) exceed max {max_allowed}"
        return True, f"stress failures {len(fails)} <= {max_allowed}"

    if ltype == "yield_gte":
        mc = ctx.monte_carlo
        if not mc:
            return False, "no monte_carlo report (run sim tolerance)"
        yield_pct = mc.get("yield_pct")
        if yield_pct is None:
            return False, "monte_carlo.yield_pct missing"
        min_pct = float(limit.get("min_pct", 100.0))
        if float(yield_pct) < min_pct:
            return False, f"yield {float(yield_pct):g}% below min {min_pct:g}%"
        return True, f"yield {float(yield_pct):g}% >= {min_pct:g}%"

    if ltype == "waveform_rmse_lte":
        max_v = float(limit.get("max_v", limit.get("max", 0.2)))
        probe_id = limit.get("probe_id") or limit.get("id")
        rows = _gate_waveform_rows(ctx.gate_report)
        if probe_id:
            rows = [r for r in rows if r.get("id") == probe_id]
        if not rows:
            return False, "no waveform comparison in gate report"
        worst = max(float(r.get("rmse") or float("inf")) for r in rows)
        if worst > max_v:
            return False, f"RMSE {worst:g} V exceeds max {max_v:g} V"
        return True, f"RMSE {worst:g} V <= {max_v:g} V"

    if ltype == "correlation_gte":
        min_corr = float(limit.get("min", 0.8))
        probe_id = limit.get("probe_id") or limit.get("id")
        rows = _gate_waveform_rows(ctx.gate_report)
        if probe_id:
            rows = [r for r in rows if r.get("id") == probe_id]
        if not rows:
            return False, "no waveform comparison in gate report"
        corrs = [
            float(r["waveform"]["correlation"])
            for r in rows
            if r.get("waveform") and r["waveform"].get("correlation") is not None
        ]
        if not corrs:
            return False, "no correlation values in gate report"
        worst = min(corrs)
        if worst < min_corr:
            return False, f"correlation {worst:g} below min {min_corr:g}"
        return True, f"correlation {worst:g} >= {min_corr:g}"

    if ltype == "sweep_metric_max_lte":
        metric = str(limit.get("metric") or limit.get("name") or "")
        if not metric:
            return False, "sweep_metric_max_lte requires metric"
        vals = _sweep_metric_values(ctx, metric)
        if not vals:
            return False, f"metric {metric!r} not found in block_sweep_report"
        worst = max(vals)
        hi = limit.get("max")
        if hi is None:
            return False, "sweep_metric_max_lte requires max"
        if worst > float(hi):
            return False, f"sweep max {worst:g} exceeds {float(hi):g}"
        return True, f"sweep max {worst:g} <= {float(hi):g}"

    if ltype == "sweep_metric_min_gte":
        metric = str(limit.get("metric") or limit.get("name") or "")
        if not metric:
            return False, "sweep_metric_min_gte requires metric"
        vals = _sweep_metric_values(ctx, metric)
        if not vals:
            return False, f"metric {metric!r} not found in block_sweep_report"
        worst = min(vals)
        lo = limit.get("min")
        if lo is None:
            return False, "sweep_metric_min_gte requires min"
        if worst < float(lo):
            return False, f"sweep min {worst:g} below {float(lo):g}"
        return True, f"sweep min {worst:g} >= {float(lo):g}"

    if ltype == "require_unit":
        expected = str(limit.get("unit") or "")
        thermal = (ctx.gate_report or {}).get("thermal") or {}
        actual = thermal.get("unit")
        flag = thermal.get("frame_unit_is_degc")
        if actual is None and flag is None:
            return False, "no thermal unit evidence in gate report (capture a thermal session)"
        if expected == "degC":
            is_degc = actual == "degC" or (isinstance(flag, Real) and float(flag) >= 0.5)
            if not is_degc:
                return False, "evidence unit is count; spec requires degC (refuse to compare)"
            slope = thermal.get("calibration_slope")
            if slope is None:
                return False, "degC evidence has no slope (calibration not persisted)"
            return True, "evidence unit is degC"
        if actual and actual != expected:
            return False, f"evidence unit {actual!r} != required {expected!r}"
        return True, f"evidence unit is {actual or expected}"

    if ltype == "thermal_delta_lte":
        thermal = (ctx.gate_report or {}).get("thermal")
        if not thermal:
            return True, "skipped (no thermal evidence)"
        peak = thermal.get("t_delta_peak")
        if peak is None:
            return True, "skipped (no t_delta_peak on thermal evidence)"
        hi = limit.get("max")
        if hi is None:
            return False, "thermal_delta_lte requires max"
        if float(peak) > float(hi):
            return False, f"t_delta_peak {float(peak):g} exceeds {float(hi):g}"
        return True, f"t_delta_peak {float(peak):g} <= {float(hi):g}"

    if ltype == "thermal_alert_clear":
        thermal = (ctx.gate_report or {}).get("thermal")
        if not thermal:
            return True, "skipped (no thermal evidence)"
        code = thermal.get("alert_severity_code")
        if code is None:
            return True, "skipped (no alert_severity_code on thermal evidence)"
        allow_warn = bool(limit.get("allow_warn", False))
        level = float(code)
        if level >= 2.0 or (level >= 1.0 and not allow_warn):
            return False, f"alert_severity_code {level:g} is not clear"
        return True, f"alert_severity_code {level:g} is clear"

    if ltype == "fixture_id_match":
        expected = str(limit.get("fixture_id") or "")
        thermal = (ctx.gate_report or {}).get("thermal") or {}
        actual = thermal.get("fixture_id")
        if not expected:
            return False, "fixture_id_match requires fixture_id"
        if not actual:
            return False, "no fixture_id on latest thermal session"
        if str(actual) != expected:
            return False, f"fixture_id {actual!r} != expected {expected!r} (cross-fixture counts)"
        return True, "fixture_id matches"

    return False, f"unknown limit type {ltype!r}"


def evaluate_rule(rule: RuleDef, pack: RulePack, ctx: RuleContext) -> RuleResult:
    if not _check_entry_matches(rule.when, ctx):
        return RuleResult(
            pack_id=pack.id,
            rule_id=rule.id,
            severity=rule.severity,
            passed=True,
            message="skipped (when clause not matched)",
            evidence=rule.evidence,
            source=pack.source,
        )
    passed, message = _evaluate_limit(rule, ctx)
    if passed:
        return RuleResult(
            pack_id=pack.id,
            rule_id=rule.id,
            severity=rule.severity,
            passed=True,
            message=message,
            evidence=rule.evidence,
            source=pack.source,
        )
    if rule.severity == "warn":
        return RuleResult(
            pack_id=pack.id,
            rule_id=rule.id,
            severity="warn",
            passed=True,
            message=f"warning: {message}",
            evidence=rule.evidence,
            source=pack.source,
        )
    return RuleResult(
        pack_id=pack.id,
        rule_id=rule.id,
        severity=rule.severity,
        passed=False,
        message=message,
        evidence=rule.evidence,
        source=pack.source,
    )


def evaluate_rule_packs(packs: list[RulePack], ctx: RuleContext, *, scope: str = "gate") -> RuleEvaluation:
    results: list[RuleResult] = []
    for pack in packs:
        if scope not in pack.applies_to:
            continue
        for rule in pack.rules:
            result = evaluate_rule(rule, pack, ctx)
            if result.message.startswith("skipped"):
                continue
            results.append(result)

    failures = sum(1 for r in results if not r.passed and r.severity == "fail")
    warnings = sum(1 for r in results if r.severity == "warn" and r.message.startswith("warning:"))
    hard_fails = [r for r in results if not r.passed and r.severity == "fail"]
    return RuleEvaluation(
        packs=[p.id for p in packs],
        passed=len(hard_fails) == 0,
        results=results,
        failures=failures,
        warnings=warnings,
    )
