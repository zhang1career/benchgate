"""End-to-end watch_once integration (no ngspice)."""

from __future__ import annotations

from pathlib import Path

import yaml

from benchgate.watch.trigger import watch_once


def test_watch_once_pipeline_mapping_gate(tmp_path: Path) -> None:
    design = tmp_path / "charge-pump"
    design.mkdir()
    (design / "charge-pump.kicad_pro").write_text("(kicad_pro (version 20250114))\n", encoding="utf-8")
    (design / "charge-pump.kicad_sch").write_text("(kicad_sch (version 20250114))\n", encoding="utf-8")

    models = design / "models"
    models.mkdir()
    manifest = models / "manifest.yaml"
    manifest.write_text("version: 2\nentries: []\n", encoding="utf-8")
    (models / "blocks.yaml").write_text(
        yaml.dump({"version": 1, "blocks": []}),
        encoding="utf-8",
    )
    (models / "auto_capture.yaml").write_text("enabled: false\n", encoding="utf-8")

    reports = design / "reports"
    state = design / ".benchgate" / "watch_state.json"
    subckt = tmp_path / "subckt"
    subckt.mkdir()
    global_models = tmp_path / "models"
    global_models.mkdir()

    result = watch_once(
        design,
        manifest_path=manifest,
        models_dir=models,
        reports_dir=reports,
        state_path=state,
        subckt_dir=subckt,
        global_models_dir=global_models,
        run_pipeline=True,
        run_sim=False,
        run_gate=True,
        run_auto_capture=False,
    )

    assert result["changed_files"]
    assert "mapping_status" in result
    assert "gate" in result
    assert (reports / "gate_report.json").is_file()
