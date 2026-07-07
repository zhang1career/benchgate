"""Tests for YAML rule packs and gate integration."""

from __future__ import annotations

import json
from pathlib import Path

from benchgate.gate.report import build_gate_report
from benchgate.rules.evaluate import RuleContext, evaluate_rule_packs
from benchgate.rules.loader import load_rule_pack
from benchgate.schemas import MappingManifest


def _examples_rules(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "examples" / "rules" / name


def test_load_corp_derating_pack():
    pack = load_rule_pack(_examples_rules("corp-derating.yaml"))
    assert pack.id == "corp-derating-2024"
    assert len(pack.rules) >= 1


def test_evaluate_project_spec_against_sim_report():
    pack = load_rule_pack(_examples_rules("project-spec.yaml"))
    sim_report = {
        "checks": {
            "checks": [
                {"signal": "v(vout)", "metric": "avg", "value": 20.1, "passed": True},
                {"signal": "v(net-_u1-out_)", "metric": "pp", "value": 11.4, "passed": True},
            ]
        }
    }
    ev = evaluate_rule_packs([pack], RuleContext(sim_report=sim_report), scope="gate")
    assert ev.passed
    assert any(r.rule_id == "vout_avg" and r.passed for r in ev.results)


def test_evaluate_stress_rules_fail_without_stress():
    pack = load_rule_pack(_examples_rules("corp-derating.yaml"))
    ev = evaluate_rule_packs([pack], RuleContext(sim_report={}), scope="gate")
    assert not ev.passed


def test_gate_report_includes_rules_summary(tmp_path):
    sim_path = tmp_path / "sim_report.json"
    sim_path.write_text(
        json.dumps(
            {
                "checks": {
                    "checks": [
                        {"signal": "v(vout)", "metric": "avg", "value": 20.0, "passed": True},
                        {"signal": "v(net-_u1-out_)", "metric": "pp", "value": 1.0, "passed": True},
                    ]
                },
                "stress": {"passed": True, "results": [], "warnings": []},
            }
        ),
        encoding="utf-8",
    )
    report = build_gate_report(
        MappingManifest(),
        captured_dir=tmp_path / "captured",
        sim_report_path=sim_path,
        rule_pack_paths=[_examples_rules("project-spec.yaml"), _examples_rules("corp-derating.yaml")],
    )
    rules = report.summary.get("rules")
    assert rules is not None
    assert rules["passed"] is True
    assert "project-spec" in rules["packs"]


def test_yield_rule_warns_without_mc():
    pack = load_rule_pack(_examples_rules("project-spec.yaml"))
    ev = evaluate_rule_packs(
        [pack],
        RuleContext(sim_report={"checks": {"checks": []}}),
        scope="gate",
    )
    mc = [r for r in ev.results if r.rule_id == "mc_yield_95"]
    assert mc and mc[0].passed and mc[0].message.startswith("warning:")
