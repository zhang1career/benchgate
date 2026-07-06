"""Tests for sim diagnose and connector preflight."""

from __future__ import annotations

import json
from pathlib import Path

from benchgate.schemas import ComponentMapping, MappingManifest, SpiceModelKind
from benchgate.sim.diagnose import diagnose_sim
from benchgate.sim.preflight import run_preflight


def test_connector_preflight_is_info() -> None:
    netlist = "J1 __J1 Connector\n"
    manifest = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="J:1",
                reference="J1",
                spice_kind=SpiceModelKind.UNMAPPED,
                metadata={"lib_id": "Connector:Conn_01x01_Pin"},
            )
        ]
    )
    report = run_preflight(netlist, manifest)
    assert report.passed
    issue = report.issues[0]
    assert issue.severity == "info"
    assert issue.code == "connector_dropped"


def test_diagnose_reads_sim_report(tmp_path: Path) -> None:
    sim_dir = tmp_path / "sim"
    sim_dir.mkdir()
    (sim_dir / "sim_report.json").write_text(
        json.dumps(
            {
                "success": False,
                "ngspice_ok": True,
                "checks": {"results": [{"passed": False, "name": "vout", "message": "out of range"}]},
            }
        ),
        encoding="utf-8",
    )
    result = diagnose_sim(tmp_path)
    assert not result["ok"]
    assert any(f["category"] == "checks" for f in result["findings"])
