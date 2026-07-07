"""Tests for simulation preflight."""

from __future__ import annotations

from pathlib import Path

from benchgate.io.manifest import MappingManifest
from benchgate.schemas import ComponentMapping, SpiceModelKind
from benchgate.sim.preflight import run_preflight


def _manifest_with_u1(lib: Path) -> MappingManifest:
    return MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="Timer:NE555D::NE555",
                reference="U1",
                spice_kind=SpiceModelKind.SUBCKT,
                sim_library=lib,
                sim_name="NE555",
            )
        ]
    )


def test_preflight_flags_ready_subckt_placeholder(tmp_path: Path) -> None:
    lib = tmp_path / "ne555.lib"
    lib.write_text("* stub\n", encoding="utf-8")
    exported = "U1 __U1\nR1 a b 1k\n"
    report = run_preflight(exported, _manifest_with_u1(lib))
    assert report.passed is False
    assert any(i.code == "unmodeled_subckt" for i in report.issues)


def test_preflight_info_on_bjt_fixup() -> None:
    prepared = "* benchgate: BJT pin order fixed for Q1\nQ1 c b e SS8050\n"
    report = run_preflight("R1 a b 1k\n", MappingManifest(), prepared_text=prepared)
    assert any(i.code == "bjt_pin_fixup" for i in report.issues)
