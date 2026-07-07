"""Tests for watch auto_capture."""

from __future__ import annotations

from pathlib import Path

from benchgate.schemas import ComponentMapping, MappingManifest, SpiceModelKind
from benchgate.watch.auto_capture import is_auto_capture_candidate, run_auto_capture


def _entry(**kwargs) -> ComponentMapping:
    base = dict(kicad_key="Q:1", reference="Q1", spice_kind=SpiceModelKind.SUBCKT)
    base.update(kwargs)
    return ComponentMapping(**base)


def test_skip_connector_refs() -> None:
    entry = _entry(reference="J1", metadata={"lib_id": "Connector:Conn_01x01_Pin"})
    assert not is_auto_capture_candidate(entry, {})


def test_pending_subckt_is_candidate() -> None:
    entry = _entry(metadata={"lib_id": "Transistor_BJT:SS8050", "value": "SS8050"})
    assert is_auto_capture_candidate(entry, {})


def test_auto_capture_dry_run(tmp_path: Path) -> None:
    design = tmp_path / "design"
    models = design / "models"
    models.mkdir(parents=True)
    manifest = MappingManifest(
        entries=[
            _entry(metadata={"lib_id": "Transistor_BJT:SS8050", "value": "SS8050"}),
        ]
    )
    result = run_auto_capture(
        design,
        manifest,
        models_dir=models,
        lab_config=models / "lab.yaml",
        instruments_config=tmp_path / "instruments.yaml",
        dry_run=True,
    )
    assert result["candidates"] == 1
    assert result["captures"][0]["status"] == "dry_run"
