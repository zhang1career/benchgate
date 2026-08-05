"""Tests for sweep aggregation and sweep rules."""

from __future__ import annotations

from pathlib import Path

from benchgate.rules.evaluate import RuleContext, evaluate_rule
from benchgate.rules.loader import RuleDef, RulePack
from benchgate.sim.sweep import SweepReport, aggregate_sweep_metrics


def test_aggregate_sweep_metrics_max_min():
    report = SweepReport(
        metric="peak_amp",
        profile="tb.cir",
        points=[
            {"overrides": {"R_ISO": "22"}, "metric": 1.8, "metrics": {"peak_amp": 1.8, "bw_com": 25e6}},
            {"overrides": {"R_ISO": "100"}, "metric": 0.5, "metrics": {"peak_amp": 0.5, "bw_com": 4e6}},
        ],
        ran_at="now",
        metrics=["peak_amp", "bw_com"],
    )
    agg = aggregate_sweep_metrics(report)
    assert agg["peak_amp_max"] == 1.8
    assert agg["peak_amp_min"] == 0.5
    assert agg["bw_com_max"] == 25e6


def test_sweep_metric_max_lte_rule():
    pack = RulePack(
        id="t",
        version=1,
        source="test",
        applies_to=["gate"],
        severity_default="fail",
        path=Path("test.yaml"),
        rules=[],
    )
    rule = RuleDef(
        id="iso_peak",
        when={},
        severity="fail",
        limit={"type": "sweep_metric_max_lte", "metric": "peak_amp", "max": 1.0},
        evidence="",
    )
    ctx = RuleContext(
        block_sweep_report={
            "metric": "peak_amp",
            "points": [
                {"metrics": {"peak_amp": 0.5}},
                {"metrics": {"peak_amp": 1.2}},
            ],
        }
    )
    result = evaluate_rule(rule, pack, ctx)
    assert result.passed is False
    assert "1.2" in result.message
