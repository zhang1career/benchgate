"""Tests for unified benchgate diagnose."""

from __future__ import annotations

import json

from benchgate.diagnose import diagnose_project


def test_diagnose_merges_gate_and_sim(tmp_path):
    reports = tmp_path / "reports"
    sim_dir = reports / "sim"
    sim_dir.mkdir(parents=True)

    (sim_dir / "preflight.json").write_text(
        json.dumps({"passed": True, "issues": []}),
        encoding="utf-8",
    )
    (sim_dir / "sim_report.json").write_text(
        json.dumps({"success": True, "checks": {"passed": True, "checks": []}}),
        encoding="utf-8",
    )
    (sim_dir / "ngspice.log").write_text("OK\n", encoding="utf-8")

    gate = {
        "entries": [
            {
                "reference": "U1",
                "spec_failures": ["eff_pct=80 below spec min 90"],
                "waveform_status": "fail",
                "rmse": 0.5,
            }
        ],
        "summary": {"comparisons": []},
    }
    (reports / "gate_report.json").write_text(json.dumps(gate), encoding="utf-8")

    result = diagnose_project(reports, captured_dir=tmp_path / "captured")
    assert result["summary"]["errors"] >= 1
    assert result["attribution"]["likely"] == "design"
    assert any(f["category"] == "spec" for f in result["findings"])
