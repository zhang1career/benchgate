"""Tests for blocks.yaml validation."""

from __future__ import annotations

from pathlib import Path

from benchgate.io.blocks_validate import validate_blocks_yaml

CHARGE_PUMP = (Path(__file__).resolve().parents[2] / "charge-pump" / "pcb").resolve()


def test_validate_charge_pump_blocks_ok():
    if not (CHARGE_PUMP / "models" / "blocks.yaml").is_file():
        return
    report = validate_blocks_yaml(
        CHARGE_PUMP,
        CHARGE_PUMP / "models" / "blocks.yaml",
        profile="charge_pump",
    )
    assert report.ok, report.to_dict()
    assert "full" in report.mc_layers
    assert "block:U1" in report.mc_layers


def test_validate_tran_stop_shorter_than_window(tmp_path):
    blocks = tmp_path / "models"
    blocks.mkdir()
    yaml_text = """
circuit_spec:
  checks:
    - id: vout
      signal: v(vout)
      metric: avg
      window_after: 40ms
      bounds: [0, 10]
tolerances:
  - ref: R1
    tolerance_pct: 1
tolerance_sim:
  coarse:
    tran_stop: 35m
"""
    (blocks / "blocks.yaml").write_text(yaml_text, encoding="utf-8")
    report = validate_blocks_yaml(tmp_path, blocks / "blocks.yaml")
    assert not report.ok
    assert any("tran_stop" in e.path for e in report.errors)


def test_validate_missing_block_source(tmp_path):
    blocks = tmp_path / "models"
    blocks.mkdir()
    yaml_text = """
blocks:
  - reference: U1
    source: blocks/missing.net
    tolerances:
      - ref: R1
        tolerance_pct: 1
"""
    (blocks / "blocks.yaml").write_text(yaml_text, encoding="utf-8")
    report = validate_blocks_yaml(tmp_path, blocks / "blocks.yaml")
    assert not report.ok
    assert any("source" in e.path for e in report.errors)
