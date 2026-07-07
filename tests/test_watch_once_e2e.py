"""End-to-end watch_once: KiCad + blocks.yaml change → pipeline → manifest → gate."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from benchgate.io.manifest import load_manifest
from benchgate.watch.trigger import watch_once

SUBCKT_NET = """.subckt BUCK vin sw gnd
R1 vin sw 100m
C1 sw gnd 10u
.ends BUCK
"""


def _write_design_skeleton(design: Path) -> tuple[Path, Path, Path, Path, Path]:
    design.mkdir(parents=True, exist_ok=True)
    (design / "board.kicad_pro").write_text("(kicad_pro (version 20250114))\n", encoding="utf-8")
    (design / "board.kicad_sch").write_text("(kicad_sch (version 20250114))\n", encoding="utf-8")

    models = design / "models"
    models.mkdir()
    manifest = models / "manifest.yaml"
    manifest.write_text("version: 2\nentries: []\n", encoding="utf-8")
    (models / "auto_capture.yaml").write_text("enabled: false\n", encoding="utf-8")

    blocks = models / "blocks"
    blocks.mkdir()
    (blocks / "buck.net").write_text(SUBCKT_NET, encoding="utf-8")
    (blocks / "buck.metrics.json").write_text(json.dumps({"eff_pct": 88, "ripple_mv": 12}), encoding="utf-8")
    (models / "blocks.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "operating_point": {"vsupply_v": 5.0, "temp_c": 25},
                "blocks": [
                    {
                        "kicad_key": "Reg:Buck::BUCK1",
                        "reference": "U3",
                        "source": "blocks/buck.net",
                        "sim_name": "BUCK",
                        "spec": {"eff_pct": [90, 100], "ripple_mv": [0, 15]},
                        "metrics_file": "blocks/buck.metrics.json",
                        "valid_range": {"vsupply_v": [4.5, 5.5]},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    reports = design / "reports"
    state = design / ".benchgate" / "watch_state.json"
    return models, manifest, reports, state, blocks


def _run_watch_once(
    design: Path,
    *,
    manifest: Path,
    models: Path,
    reports: Path,
    state: Path,
    subckt: Path,
    global_models: Path,
) -> dict:
    return watch_once(
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


def test_watch_once_pipeline_mapping_gate(tmp_path: Path) -> None:
    design = tmp_path / "design"
    models, manifest, reports, state, _ = _write_design_skeleton(design)
    subckt = tmp_path / "subckt"
    subckt.mkdir()
    global_models = tmp_path / "models"
    global_models.mkdir()

    result = _run_watch_once(
        design,
        manifest=manifest,
        models=models,
        reports=reports,
        state=state,
        subckt=subckt,
        global_models=global_models,
    )

    assert result["changed_files"]
    assert "mapping_status" in result
    assert "gate" in result
    assert (reports / "gate_report.json").is_file()


def test_watch_once_blocks_change_updates_manifest_and_gate(tmp_path: Path, monkeypatch) -> None:
    """Regression: edit blocks metrics → watch_once re-syncs pipeline and gate verdict."""
    home = tmp_path / "home"
    monkeypatch.setenv("BENCHGATE_HOME", str(home))

    design = tmp_path / "design"
    models, manifest, reports, state, blocks_dir = _write_design_skeleton(design)
    subckt = home / "models" / "subckt"
    subckt.mkdir(parents=True)
    global_models = home / "models"

    first = _run_watch_once(
        design,
        manifest=manifest,
        models=models,
        reports=reports,
        state=state,
        subckt=subckt,
        global_models=global_models,
    )
    assert first["pipeline"]["blocks_synced"] == 1
    assert first["gate"]["summary"]["spec_failures"] == 1

    entry = load_manifest(manifest, global_models_dir=global_models).find("Reg:Buck::BUCK1")
    assert entry is not None
    assert entry.is_ready
    assert entry.provenance.metrics["eff_pct"] == 88.0

    # Simulate designer improving local block metrics (top-down iteration).
    (blocks_dir / "buck.metrics.json").write_text(json.dumps({"eff_pct": 95, "ripple_mv": 10}), encoding="utf-8")

    second = _run_watch_once(
        design,
        manifest=manifest,
        models=models,
        reports=reports,
        state=state,
        subckt=subckt,
        global_models=global_models,
    )
    assert any("buck.metrics.json" in p for p in second["changed_files"])
    assert second["pipeline"]["blocks_synced"] == 1
    assert second["gate"]["summary"]["spec_failures"] == 0

    entry = load_manifest(manifest, global_models_dir=global_models).find("Reg:Buck::BUCK1")
    assert entry.provenance.metrics["eff_pct"] == 95.0


def test_watch_once_blocks_yaml_spec_change(tmp_path: Path, monkeypatch) -> None:
    """Changing spec budget in blocks.yaml is picked up on next watch_once."""
    home = tmp_path / "home"
    monkeypatch.setenv("BENCHGATE_HOME", str(home))

    design = tmp_path / "design"
    models, manifest, reports, state, _ = _write_design_skeleton(design)
    subckt = home / "models" / "subckt"
    subckt.mkdir(parents=True)
    global_models = home / "models"

    _run_watch_once(
        design,
        manifest=manifest,
        models=models,
        reports=reports,
        state=state,
        subckt=subckt,
        global_models=global_models,
    )

    data = yaml.safe_load((models / "blocks.yaml").read_text(encoding="utf-8"))
    data["blocks"][0]["spec"] = {"eff_pct": [85, 100]}
    (models / "blocks.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    third = _run_watch_once(
        design,
        manifest=manifest,
        models=models,
        reports=reports,
        state=state,
        subckt=subckt,
        global_models=global_models,
    )
    assert any("blocks.yaml" in p for p in third["changed_files"])
    assert third["gate"]["summary"]["spec_failures"] == 0

    entry = load_manifest(manifest, global_models_dir=global_models).find("Reg:Buck::BUCK1")
    assert entry.spec == {"eff_pct": [85, 100]}
