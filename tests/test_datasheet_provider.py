"""Tests for DatasheetModelProvider and build_model."""

from __future__ import annotations

from pathlib import Path

from benchgate.agent.dispatch import dispatch
from benchgate.io.manifest import load_manifest
from benchgate.mapping.engine import build_model, ensure_datasheet_models
from benchgate.providers.datasheet import DatasheetModelProvider
from benchgate.schemas import ComponentMapping, MappingManifest, ModelSource, SpiceModelKind


def test_datasheet_provider_writes_model(tmp_path: Path) -> None:
    catalog = tmp_path / "datasheet_models.yaml"
    catalog.write_text(
        "1N4001:\n  element: D\n  params: IS=14.11n RS=0.2818 BV=75\n",
        encoding="utf-8",
    )
    subckt = tmp_path / "subckt"
    entry = ComponentMapping(kicad_key="Diode:1N4007::1N4001", reference="D2")
    provider = DatasheetModelProvider(mpn="1N4001", catalog_path=catalog)
    art = provider.build(entry, workdir=subckt)
    text = art.lib_path.read_text(encoding="utf-8")
    assert ".model 1N4001 D" in text
    assert art.sim_name == "1N4001"
    assert art.provenance.source == ModelSource.DATASHEET


def test_build_model_registers_manifest(tmp_path: Path) -> None:
    catalog = tmp_path / "datasheet_models.yaml"
    catalog.write_text(
        "1N4001:\n  element: D\n  params: IS=14.11n RS=0.2818 BV=75\n",
        encoding="utf-8",
    )
    manifest = MappingManifest()
    entry = ComponentMapping(kicad_key="Diode:1N4007::1N4001", reference="D2")
    provider = DatasheetModelProvider(mpn="1N4001", catalog_path=catalog)
    build_model(manifest, entry, provider, workdir=tmp_path / "subckt")
    assert entry.is_ready
    assert entry.sim_name == "1N4001"
    assert entry.provenance is not None


def test_ensure_datasheet_models_on_sync(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BENCHGATE_HOME", str(tmp_path / "home"))
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "datasheet_models.yaml").write_text(
        "1N4001:\n  element: D\n  params: IS=14.11n RS=0.2818 BV=75\n",
        encoding="utf-8",
    )

    manifest = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="Diode:1N4007::1N4001",
                reference="D2",
                spice_kind=SpiceModelKind.SUBCKT,
                metadata={"value": "1N4001"},
            )
        ]
    )
    n = ensure_datasheet_models(manifest, tmp_path / "subckt")
    assert n == 1
    assert manifest.entries[0].is_ready
    assert manifest.entries[0].sim_name == "1N4001"


def test_dispatch_model_build_datasheet(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    design = tmp_path / "design"
    design.mkdir()
    (design / "charge-pump.kicad_pro").write_text("(kicad_pro)\n")
    monkeypatch.setenv("BENCHGATE_HOME", str(home))
    (home / "config").mkdir(parents=True)
    (home / "models" / "subckt").mkdir(parents=True)
    (home / "config" / "datasheet_models.yaml").write_text(
        "1N4001:\n  element: D\n  params: IS=14.11n RS=0.2818 BV=75\n",
        encoding="utf-8",
    )
    key = "Diode:1N4007::1N4001"
    result = dispatch(
        "model_build",
        {
            "design_dir": str(design),
            "kicad_key": key,
            "provider": "datasheet",
            "mpn": "1N4001",
            "reference": "D2",
        },
    )
    assert result["sim_name"] == "1N4001"
    assert result["source"] == "datasheet"
    manifest = load_manifest(design / "models" / "manifest.yaml", global_models_dir=home / "models")
    entry = manifest.find(key)
    assert entry is not None
    assert entry.is_ready
