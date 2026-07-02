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
    text, warnings = netlist_to_subckt(FLAT_NET, name="RCLP", pins=["in", "out"])
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
    text, warnings = netlist_to_subckt(SUBCKT_NET, name="RCLP", pins=None)
    assert ".subckt RCLP in out" in text
    assert ".ends RCLP" in text
    assert ".tran" not in text


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

    manifest = load_manifest(design / "models" / "manifest.yaml", global_models_dir=home / "models")
    entry = manifest.find("Simulation_SPICE:X::RCLP")
    assert entry is not None
    assert entry.spice_kind == SpiceModelKind.SUBCKT
    assert entry.is_ready
    assert entry.provenance is not None
    assert entry.provenance.source == ModelSource.LTSPICE

    status = dispatch("model_status", {"design_dir": str(design)})
    assert status["entries"][0]["source"] == "ltspice"
