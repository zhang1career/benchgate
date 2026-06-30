"""Tests for mapping and netlist injection (no KiCad CLI / ngspice required)."""

from pathlib import Path

import pytest

from benchgate.io.manifest import load_manifest, save_manifest
from benchgate.mapping.engine import infer_kind, mapping_status, sync_project
from benchgate.schemas import ComponentMapping, MappingManifest, SpiceModelKind, kicad_key
from benchgate.sim.netlist import inject_models


def test_kicad_key():
    assert kicad_key("Device:R", "10k") == "Device:R::10k"
    assert kicad_key("Device:R", "") == "Device:R"


def test_infer_kind():
    assert infer_kind("Device:R") == SpiceModelKind.PASSIVE
    assert infer_kind("Amplifier_Operational:LM358") == SpiceModelKind.SUBCKT


def test_manifest_roundtrip(tmp_path: Path):
    m = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="Device:R::10k",
                reference="R1",
                spice_kind=SpiceModelKind.PASSIVE,
            )
        ]
    )
    path = tmp_path / "models" / "manifest.yaml"
    save_manifest(m, path, global_models_dir=tmp_path / "global" / "models")
    loaded = load_manifest(path, global_models_dir=tmp_path / "global" / "models")
    entry = loaded.find("Device:R::10k")
    assert entry is not None
    assert entry.reference == "R1"
    assert entry.status == "ready"


def test_manifest_global_subckt_paths(tmp_path: Path):
    global_models = tmp_path / "global" / "models"
    subckt = global_models / "subckt" / "ucc27211.lib"
    subckt.parent.mkdir(parents=True)
    subckt.write_text(".subckt UCC27211\n.ends\n")

    manifest_path = tmp_path / "models" / "manifest.yaml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        """
version: 1
entries:
- kicad_key: Driver:UCC27211::UCC27211
  spice_kind: subckt
  sim_library: subckt/ucc27211.lib
  sim_name: UCC27211
  status: ready
""".strip()
    )

    loaded = load_manifest(manifest_path, global_models_dir=global_models)
    entry = loaded.find("Driver:UCC27211::UCC27211")
    assert entry is not None
    assert entry.sim_library == subckt.resolve()


def test_inject_models(tmp_path: Path):
    subckt = tmp_path / "u1.lib"
    subckt.write_text(".subckt U1 a b\n.ends\n")
    manifest = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="Amplifier_Operational:LM358::LM358",
                spice_kind=SpiceModelKind.SUBCKT,
                sim_library=subckt,
                sim_name="U1",
            )
        ]
    )
    out = inject_models("* netlist\nR1 1 0 1k\n", manifest)
    assert "benchgate auto-generated" in out
    assert str(subckt.resolve()) in out


def test_mapping_status():
    m = MappingManifest(
        entries=[
            ComponentMapping(kicad_key="x", spice_kind=SpiceModelKind.UNMAPPED),
            ComponentMapping(kicad_key="y", spice_kind=SpiceModelKind.PASSIVE),
        ]
    )
    s = mapping_status(m)
    assert "x" in s["unmapped"]
    assert "y" in s["ready"]


try:
    import kicad_tools  # noqa: F401

    HAS_KICAD_TOOLS = True
except ImportError:
    HAS_KICAD_TOOLS = False


@pytest.mark.skipif(not HAS_KICAD_TOOLS, reason="kicad-tools not installed")
def test_sync_empty_project(tmp_path: Path):
    from kicad_tools import Project

    Project.create("board", tmp_path)
    manifest_path = tmp_path / "models" / "manifest.yaml"
    models_dir = tmp_path / "models"
    subckt_dir = tmp_path / "global" / "models" / "subckt"
    subckt_dir.mkdir(parents=True)
    manifest = sync_project(
        tmp_path,
        manifest_path,
        models_dir,
        subckt_dir=subckt_dir,
        global_models_dir=subckt_dir.parent,
    )
    assert manifest.entries == []
    assert manifest_path.exists()


HBRIDGE_DESIGN = Path(__file__).resolve().parents[2] / "dcdc" / "h-bridge" / "h-bridge-pcb"


@pytest.mark.skipif(not HAS_KICAD_TOOLS, reason="kicad-tools not installed")
@pytest.mark.skipif(not HBRIDGE_DESIGN.is_dir(), reason="h-bridge design not present")
def test_sync_hierarchical_project(tmp_path: Path):
    from benchgate.kicad.project import KiCadProject, iter_symbols

    project = KiCadProject.load(HBRIDGE_DESIGN)
    syms = iter_symbols(project.schematic_doc(), project.root)
    assert len(syms) >= 50

    manifest_path = tmp_path / "models" / "manifest.yaml"
    subckt_dir = tmp_path / "global" / "models" / "subckt"
    subckt_dir.mkdir(parents=True)
    manifest = sync_project(
        HBRIDGE_DESIGN,
        manifest_path,
        tmp_path / "models",
        subckt_dir=subckt_dir,
        global_models_dir=subckt_dir.parent,
    )
    assert len(manifest.entries) > 0
    status = mapping_status(manifest)
    assert len(status["ready"]) + len(status["pending"]) + len(status["unmapped"]) == len(
        manifest.entries
    )
    lib_ids = {e.metadata.get("lib_id") for e in manifest.entries}
    assert "Transistor_FET:IRLZ44N" in lib_ids
    assert "basic_components:UCC27211" in lib_ids
