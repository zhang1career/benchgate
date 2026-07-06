"""Tests for the LTspice → ngspice model provider (M2)."""

from __future__ import annotations

import pytest

from benchgate.agent.dispatch import dispatch
from benchgate.io.manifest import load_manifest
from benchgate.providers.ltspice import (
    LtspiceModelProvider,
    netlist_to_subckt,
    normalize_ltspice_netlist,
)
from benchgate.schemas import ModelSource, SpiceModelKind

FLAT_NET = """* RC low-pass exported from LTspice
V1 in 0 PULSE(0 5 0 1n 1n 1m 2m)
R1 in out 1k
C1 out 0 100n
.tran 5m
.backanno
.end
"""

SUBCKT_NET = """* a block the user built as a subckt in LTspice
.subckt RCLP in out
R1 in out 1k
C1 out 0 100n
.ends RCLP
.tran 5m
"""


def test_normalize_strips_analysis_and_micro():
    lines, warnings = normalize_ltspice_netlist("R1 a b 4.7\u00b5\n.tran 1m\n.backanno\n")
    joined = "\n".join(lines)
    assert "4.7u" in joined
    assert ".tran" not in joined
    assert ".backanno" not in joined
    assert any("tran" in w for w in warnings)


def test_wrap_flat_netlist_drops_sources():
    text, warnings, sim_name, pin_names = netlist_to_subckt(FLAT_NET, name="RCLP", pins=["in", "out"])
    assert sim_name == "RCLP"
    assert pin_names == ["in", "out"]
    assert text.startswith(".subckt RCLP in out")
    assert "R1 in out 1k" in text
    assert "C1 out 0 100n" in text
    assert ".ends RCLP" in text
    # stimulus + analysis stripped
    assert "V1" not in text
    assert ".tran" not in text
    assert any("V1" in w for w in warnings)


def test_flat_netlist_without_pins_raises():
    with pytest.raises(ValueError, match="pins"):
        netlist_to_subckt(FLAT_NET, name="RCLP", pins=None)


def test_extract_existing_subckt():
    text, warnings, sim_name, pin_names = netlist_to_subckt(SUBCKT_NET, name="RCLP", pins=None)
    assert sim_name == "RCLP"
    assert pin_names == ["in", "out"]
    assert ".subckt RCLP in out" in text
    assert ".ends RCLP" in text
    assert ".tran" not in text


def test_extracted_subckt_pins_authoritative_over_cli_pins(tmp_path):
    """Netlist header pins win over --pins in extract mode (Bugbot: sim_pins mismatch)."""
    net = tmp_path / "block.net"
    net.write_text(SUBCKT_NET, encoding="utf-8")
    art = LtspiceModelProvider(net_path=net, sim_name="RCLP", pins=["a", "b"]).build(
        entry=None, workdir=tmp_path / "subckt"
    )
    assert art.sim_pins == "in out"
    assert "pins" in (art.provenance.notes or "")


def test_extracted_subckt_pins_registered_without_cli_pins(tmp_path):
    net = tmp_path / "block.net"
    net.write_text(SUBCKT_NET, encoding="utf-8")
    art = LtspiceModelProvider(net_path=net, sim_name="RCLP").build(
        entry=None, workdir=tmp_path / "subckt"
    )
    assert art.sim_pins == "in out"


def test_model_build_null_metrics_preserves_existing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    design = tmp_path / "design"
    design.mkdir()
    monkeypatch.setenv("BENCHGATE_HOME", str(home))
    (design / "block.net").write_text(SUBCKT_NET, encoding="utf-8")
    key = "Sim:X::RCLP"
    base = {"design_dir": str(design), "kicad_key": key, "source_file": "block.net", "sim_name": "RCLP"}
    dispatch("model_build", {**base, "metrics": {"eff_pct": 88.0}})
    dispatch("model_build", {**base, "metrics": None})

    entry = load_manifest(design / "models" / "manifest.yaml", global_models_dir=home / "models").find(key)
    assert entry.provenance.metrics == {"eff_pct": 88.0}
    assert entry.sim_pins == "in out"


def test_extracted_subckt_name_authoritative_over_requested(tmp_path):
    """Netlist .subckt name wins over --sim-name (Bugbot: manifest/ngspice mismatch)."""
    net = tmp_path / "block.net"
    net.write_text(SUBCKT_NET, encoding="utf-8")
    provider = LtspiceModelProvider(net_path=net, sim_name="BUCK")
    art = provider.build(entry=None, workdir=tmp_path / "subckt")
    assert art.sim_name == "RCLP"
    assert art.lib_path.name == "RCLP.lib"
    assert ".subckt RCLP" in art.lib_path.read_text()


def test_model_build_rebuild_preserves_metrics(tmp_path, monkeypatch):
    """Re-running model build without --metrics must not wipe prior gate data."""
    home = tmp_path / "home"
    design = tmp_path / "design"
    design.mkdir()
    monkeypatch.setenv("BENCHGATE_HOME", str(home))
    (design / "block.net").write_text(SUBCKT_NET, encoding="utf-8")
    key = "Sim:X::RCLP"
    base = {
        "design_dir": str(design),
        "kicad_key": key,
        "source_file": "block.net",
        "sim_name": "RCLP",
    }
    dispatch("model_build", {**base, "metrics": {"eff_pct": 88.0}, "valid_range": {"temp_c": [-10, 85]}})
    dispatch("model_build", base)

    entry = load_manifest(design / "models" / "manifest.yaml", global_models_dir=home / "models").find(key)
    assert entry.provenance.metrics == {"eff_pct": 88.0}
    assert entry.provenance.valid_range == {"temp_c": [-10, 85]}


def test_provider_build_writes_lib_and_provenance(tmp_path):
    net = tmp_path / "block.net"
    net.write_text(SUBCKT_NET, encoding="utf-8")
    provider = LtspiceModelProvider(net_path=net, sim_name="RCLP", valid_range={"temp_c": [0, 85]})
    art = provider.build(entry=None, workdir=tmp_path / "subckt")  # entry unused for ltspice
    assert art.lib_path.exists()
    assert art.sim_name == "RCLP"
    assert art.provenance.source == ModelSource.LTSPICE
    assert art.provenance.checksum
    assert art.provenance.valid_range == {"temp_c": [0, 85]}


def test_model_build_dispatch_registers_manifest(tmp_path, monkeypatch):
    home = tmp_path / "home"
    design = tmp_path / "design"
    design.mkdir()
    monkeypatch.setenv("BENCHGATE_HOME", str(home))
    net = design / "block.net"
    net.write_text(SUBCKT_NET, encoding="utf-8")

    result = dispatch(
        "model_build",
        {
            "design_dir": str(design),
            "kicad_key": "Simulation_SPICE:X::RCLP",
            "reference": "X1",
            "source_file": "block.net",
            "sim_name": "RCLP",
        },
    )
    assert result["source"] == "ltspice"
    assert result["sim_pins"] == "in out"

    manifest = load_manifest(design / "models" / "manifest.yaml", global_models_dir=home / "models")
    entry = manifest.find("Simulation_SPICE:X::RCLP")
    assert entry is not None
    assert entry.spice_kind == SpiceModelKind.SUBCKT
    assert entry.is_ready
    assert entry.provenance is not None
    assert entry.provenance.source == ModelSource.LTSPICE

    status = dispatch("model_status", {"design_dir": str(design)})
    assert status["entries"][0]["source"] == "ltspice"


def test_spec_set_and_metrics_close_loop(tmp_path, monkeypatch):
    """Top-down: spec set → model build --metrics → gate fail/pass."""
    from benchgate.gate.report import build_gate_report

    home = tmp_path / "home"
    design = tmp_path / "design"
    design.mkdir()
    monkeypatch.setenv("BENCHGATE_HOME", str(home))
    (design / "block.net").write_text(SUBCKT_NET, encoding="utf-8")
    key = "Sim:X::RCLP"

    # 1. downward: set the performance budget
    dispatch("spec_set", {"design_dir": str(design), "kicad_key": key, "reference": "X1",
                          "spec": {"eff_pct": [90, 100]}})
    # 2/3. upward: local sim result → metrics (failing: 80 < 90)
    dispatch("model_build", {"design_dir": str(design), "kicad_key": key,
                             "source_file": "block.net", "sim_name": "RCLP",
                             "metrics": {"eff_pct": 80.0}})

    manifest = load_manifest(design / "models" / "manifest.yaml", global_models_dir=home / "models")
    entry = manifest.find(key)
    assert entry.spec == {"eff_pct": [90, 100]}
    assert entry.provenance.metrics == {"eff_pct": 80.0}

    report = build_gate_report(manifest, captured_dir=design / "models" / "captured")
    assert report.entries[0].spec_status == "fail"
    assert report.summary["spec_failures"] == 1
