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


def test_default_rule_packs_never_come_from_bundled_examples(tmp_path):
    """docs/examples/rules is documentation, not a default.

    Falling back to it made a project inherit the charge-pump example's checks,
    and made every project inherit corp-derating-2024 whose stress rule hard-fails
    unless the project runs stress sweeps.
    """
    from benchgate.rules.loader import default_rule_pack_paths

    home = tmp_path / "home"
    design = tmp_path / "design"
    (design / "models").mkdir(parents=True)
    (design / "models" / "blocks.yaml").write_text("blocks: []\n", encoding="utf-8")

    # a design with a blocks.yaml but no rules of its own gets no rules at all
    assert default_rule_pack_paths(home=home, design=design) == []

    # its own pack is the only one applied
    rules_dir = design / "models" / "rules"
    rules_dir.mkdir()
    own = rules_dir / "project-spec.yaml"
    own.write_text("id: mine\nversion: 1\nrules: []\n", encoding="utf-8")
    assert default_rule_pack_paths(home=home, design=design) == [own]

    # site-wide packs come first, so a design pack can be read as the override
    site_dir = home / "config" / "rules"
    site_dir.mkdir(parents=True)
    site = site_dir / "corp-derating.yaml"
    site.write_text("id: corp\nversion: 1\nrules: []\n", encoding="utf-8")
    assert default_rule_pack_paths(home=home, design=design) == [site, own]


def test_yield_rule_warns_without_mc():
    pack = load_rule_pack(_examples_rules("project-spec.yaml"))
    ev = evaluate_rule_packs(
        [pack],
        RuleContext(sim_report={"checks": {"checks": []}}),
        scope="gate",
    )
    mc = [r for r in ev.results if r.rule_id == "mc_yield_95"]
    assert mc and mc[0].passed and mc[0].message.startswith("warning:")


def test_waveform_rmse_rule_pass_and_fail():
    pack = load_rule_pack(_examples_rules("bench-waveform.yaml"))
    gate_ok = {
        "comparisons": [{"id": "vout", "rmse": 0.05, "waveform": {"correlation": 0.99}}],
    }
    ev_ok = evaluate_rule_packs([pack], RuleContext(gate_report=gate_ok), scope="gate")
    rmse_ok = [r for r in ev_ok.results if r.rule_id == "waveform_rmse_board"]
    assert rmse_ok and rmse_ok[0].passed

    gate_bad = {
        "comparisons": [{"id": "vout", "rmse": 0.35, "waveform": {"correlation": 0.99}}],
    }
    ev_bad = evaluate_rule_packs([pack], RuleContext(gate_report=gate_bad), scope="gate")
    rmse_bad = [r for r in ev_bad.results if r.rule_id == "waveform_rmse_board"]
    assert rmse_bad and not rmse_bad[0].passed


def test_correlation_gte_rule_warns_on_low_corr():
    pack = load_rule_pack(_examples_rules("bench-waveform.yaml"))
    gate = {
        "comparisons": [{"id": "vout", "rmse": 0.05, "waveform": {"correlation": 0.5}}],
    }
    ev = evaluate_rule_packs([pack], RuleContext(gate_report=gate), scope="gate")
    corr = [r for r in ev.results if r.rule_id == "waveform_correlation_board"]
    assert corr and corr[0].passed and corr[0].message.startswith("warning:")


def test_load_bench_waveform_rules_pack():
    pack = load_rule_pack(_examples_rules("bench-waveform.yaml"))
    assert pack.id == "bench-waveform"
    assert any(r.limit.get("type") == "waveform_rmse_lte" for r in pack.rules)


def test_require_unit_fails_on_counts():
    pack = load_rule_pack(_examples_rules("thermal-unit.yaml"))
    ev = evaluate_rule_packs(
        [pack],
        RuleContext(gate_report={"thermal": {"unit": "count", "frame_unit_is_degc": 0.0, "fixture_id": "abc"}}),
        scope="gate",
    )
    unit = [r for r in ev.results if r.rule_id == "require_degc_for_temp_spec"]
    assert unit and not unit[0].passed
    assert "count" in unit[0].message


def test_require_unit_passes_on_degc():
    pack = load_rule_pack(_examples_rules("thermal-unit.yaml"))
    ev = evaluate_rule_packs(
        [pack],
        RuleContext(
            gate_report={
                "thermal": {
                    "unit": "degC",
                    "frame_unit_is_degc": 1.0,
                    "fixture_id": "REPLACE_ME",
                    "calibration_slope": 0.1,
                    "calibration_offset": -273.15,
                }
            }
        ),
        scope="gate",
    )
    unit = [r for r in ev.results if r.rule_id == "require_degc_for_temp_spec"]
    assert unit and unit[0].passed
    fix = [r for r in ev.results if r.rule_id == "fixture_id_stable"]
    assert fix and fix[0].passed


def test_require_unit_fails_degc_without_slope():
    pack = load_rule_pack(_examples_rules("thermal-unit.yaml"))
    ev = evaluate_rule_packs(
        [pack],
        RuleContext(gate_report={"thermal": {"unit": "degC", "frame_unit_is_degc": 1.0, "fixture_id": "abc"}}),
        scope="gate",
    )
    unit = [r for r in ev.results if r.rule_id == "require_degc_for_temp_spec"]
    assert unit and not unit[0].passed
    assert "slope" in unit[0].message
