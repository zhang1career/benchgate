"""Tests for agent pipeline (blocks.yaml automation)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from benchgate.agent.dispatch import dispatch
from benchgate.gate.report import build_gate_report
from benchgate.io.manifest import load_manifest
from benchgate.pipeline.local_blocks import load_blocks_config, sync_local_blocks
from benchgate.schemas import ModelSource
from benchgate.watch.trigger import detect_changes, pipeline_files

SUBCKT_NET = """.subckt BUCK vin sw gnd
R1 vin sw 100m
C1 sw gnd 10u
.ends BUCK
"""


def _write_blocks_setup(models: Path) -> None:
    blocks = models / "blocks"
    blocks.mkdir(parents=True)
    (blocks / "buck.net").write_text(SUBCKT_NET, encoding="utf-8")
    (blocks / "buck.metrics.json").write_text(json.dumps({"eff_pct": 88, "ripple_mv": 12}), encoding="utf-8")
    (models / "blocks.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "operating_point": {"vsupply_v": 5.0},
                "blocks": [
                    {
                        "kicad_key": "Reg:Buck::BUCK1",
                        "reference": "U3",
                        "source": "blocks/buck.net",
                        "sim_name": "BUCK",
                        "spec": {"eff_pct": [90, 100]},
                        "metrics_file": "blocks/buck.metrics.json",
                        "valid_range": {"vsupply_v": [4.5, 5.5]},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_load_blocks_config(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    _write_blocks_setup(models)
    op, blocks = load_blocks_config(models / "blocks.yaml")
    assert op["vsupply_v"] == 5.0
    assert len(blocks) == 1
    assert blocks[0]["kicad_key"] == "Reg:Buck::BUCK1"


def test_pipeline_sync_builds_manifest_and_spec_gate(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    design = tmp_path / "design"
    models = design / "models"
    models.mkdir(parents=True)
    monkeypatch.setenv("BENCHGATE_HOME", str(home))
    _write_blocks_setup(models)

    result = sync_local_blocks(
        models_dir=models,
        manifest_path=models / "manifest.yaml",
        subckt_dir=home / "models" / "subckt",
        global_models_dir=home / "models",
    )
    assert result["skipped"] is False
    assert result["blocks_synced"] == 1
    assert result["blocks"][0]["ok"] is True
    assert result["blocks"][0]["sim_name"] == "BUCK"
    assert result["blocks"][0]["sim_pins"] == "vin sw gnd"

    manifest = load_manifest(models / "manifest.yaml", global_models_dir=home / "models")
    entry = manifest.find("Reg:Buck::BUCK1")
    assert entry is not None
    assert entry.is_ready
    assert entry.spec == {"eff_pct": [90, 100]}
    assert entry.provenance.metrics == {"eff_pct": 88.0, "ripple_mv": 12.0}
    assert entry.provenance.source == ModelSource.LTSPICE

    report = build_gate_report(
        manifest,
        captured_dir=models / "captured",
        operating_point=result["operating_point"],
    )
    assert report.entries[0].spec_status == "fail"
    assert report.summary["spec_failures"] == 1


def test_pipeline_sync_dispatch(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    design = tmp_path / "design"
    models = design / "models"
    models.mkdir(parents=True)
    monkeypatch.setenv("BENCHGATE_HOME", str(home))
    _write_blocks_setup(models)

    out = dispatch("pipeline_sync", {"design_dir": str(design)})
    assert out["blocks_synced"] == 1


def test_watch_detects_blocks_files(tmp_path: Path):
    design = tmp_path / "design"
    models = design / "models"
    models.mkdir(parents=True)
    _write_blocks_setup(models)
    state = tmp_path / "state.json"

    changed = detect_changes(design, state)
    assert any("blocks.yaml" in str(p) for p in changed)
    assert any("buck.net" in str(p) for p in changed)
    assert len(pipeline_files(design)) >= 2

    assert detect_changes(design, state) == []
