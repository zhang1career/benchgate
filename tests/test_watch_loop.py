"""Tests for continuous watch loop."""

from __future__ import annotations

from pathlib import Path

from benchgate.watch.loop import watch_loop


def test_watch_loop_single_iteration(tmp_path: Path) -> None:
    design = tmp_path / "design"
    design.mkdir()
    (design / "board.kicad_pro").write_text("(kicad_pro)\n")
    (design / "board.kicad_sch").write_text("(kicad_sch)\n")

    models = design / "models"
    models.mkdir()
    manifest = models / "manifest.yaml"
    manifest.write_text("version: 2\nentries: []\n")
    reports = design / "reports"
    state = design / ".benchgate" / "watch_state.json"

    result = watch_loop(
        design,
        manifest_path=manifest,
        models_dir=models,
        reports_dir=reports,
        state_path=state,
        subckt_dir=tmp_path / "subckt",
        global_models_dir=tmp_path / "models",
        run_pipeline=False,
        run_sim=False,
        run_gate=False,
        interval_s=0.01,
        debounce_s=0.0,
        max_iterations=1,
    )
    assert result["iterations"] == 1
    assert "last" in result
