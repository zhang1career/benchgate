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
