"""Tests for manifest subckt injection and DNP stripping."""

from __future__ import annotations

from pathlib import Path

from benchgate.schemas import ComponentMapping, MappingManifest, SpiceModelKind
from benchgate.sim.netlist_fixup import (
    NetlistFixupConfig,
    apply_netlist_fixups,
    inject_manifest_subckts,
    strip_dnp_elements,
)

CHARGE_PUMP_NET = """
Q1 Net-_Q1-B_ Net-_Q1-E_ VIN SS8050
R6 Net-_C2-Pad1_ Net-_U1-TRIG_ 0
R5 Net-_C2-Pad1_ Net-_U1-THRES_ 0
R2 Net-_C2-Pad1_ Net-_U1-DISCH_ 22k
U1 __U1
R8 GND Net-_U1-TRIG_ DNP
R7 Net-_U1-TRIG_ VIN DNP
R4 VIN /rst 10k
C3 Net-_U1-CONT_ GND 0.01u
R3 Net-_U1-OUT_ Net-_Q1-B_ 100
C1 VIN GND 100u
.end
"""


def _u1_manifest() -> MappingManifest:
    return MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="Timer:NE555D::NE555",
                reference="U1",
                spice_kind=SpiceModelKind.SUBCKT,
                sim_library=Path("/tmp/ne555.lib"),
                sim_name="NE555",
                sim_pins="1=GND 2=TRIG 3=OUT 4=RST 5=CONT 6=THR 7=DIS 8=VCC",
            ),
            ComponentMapping(
                kicad_key="Transistor_BJT:SS8050::SS8050",
                reference="Q1",
                spice_kind=SpiceModelKind.SUBCKT,
                sim_pins="1=B 2=E 3=C",
            ),
        ]
    )


def test_inject_manifest_subckt_u1() -> None:
    manifest = _u1_manifest()
    manifest.entries[0].sim_library = Path(__file__).resolve().parents[1] / "docs" / "examples" / "x.lib"
    # is_ready requires sim_library.exists(); patch by using real bundled path
    from benchgate.paths import benchgate_home

    lib = benchgate_home() / "models" / "subckt" / "ne555.lib"
    if lib.exists():
        manifest.entries[0].sim_library = lib
    else:
        manifest.entries[0].spice_kind = SpiceModelKind.SUBCKT
        manifest.entries[0].sim_library = Path("/nonexistent/ne555.lib")

    out = inject_manifest_subckts(CHARGE_PUMP_NET, manifest)
    if lib.exists():
        assert "XU1 GND Net-_U1-TRIG_ Net-_U1-OUT_ /rst Net-_U1-CONT_ Net-_U1-THRES_ Net-_U1-DISCH_ VIN NE555" in out
        assert "U1 __U1" not in out


def test_strip_dnp_elements() -> None:
    out = strip_dnp_elements("R7 Net-_U1-TRIG_ VIN DNP\nR1 VIN GND 10k\n")
    assert "benchgate: DNP omitted" in out
    assert "R7 Net-_U1-TRIG_ VIN DNP" not in out
    assert "R1 VIN GND 10k" in out
    tagged = strip_dnp_elements("C90 GIC_A GIC_B DNP_GIC\nR90 GND MODE DNP_MODE\n")
    assert "C90 GIC_A GIC_B DNP_GIC" not in tagged
    assert "R90 GND MODE DNP_MODE" not in tagged


def test_inject_diode_models() -> None:
    from benchgate.sim.netlist_fixup import inject_diode_models

    net = ".model __D1 D\n.model __D2 D\nD1 VIN Net-_D1-K_ __D1\nD2 Net-_D1-K_ VOUT __D2\n"
    manifest = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="Diode:1N4007::1N4007",
                reference="D1",
                spice_kind=SpiceModelKind.SUBCKT,
                sim_library=Path.home() / ".benchgate/models/subckt/1N4001.lib",
                sim_name="1N4001",
            ),
            ComponentMapping(
                kicad_key="Diode:1N4007::1N4001",
                reference="D2",
                spice_kind=SpiceModelKind.SUBCKT,
                sim_library=Path.home() / ".benchgate/models/subckt/1N4001.lib",
                sim_name="1N4001",
            ),
        ]
    )
    out = inject_diode_models(net, manifest)
    assert "D1 VIN Net-_D1-K_ 1N4001" in out
    assert "D2 Net-_D1-K_ VOUT 1N4001" in out
    assert "__D1\n" not in out and "__D2\n" not in out


def test_apply_netlist_fixups_full() -> None:
    manifest = _u1_manifest()
    lib = Path.home() / ".benchgate" / "models" / "subckt" / "ne555.lib"
    if lib.exists():
        manifest.entries[0].sim_library = lib
    cfg = NetlistFixupConfig()
    out = apply_netlist_fixups(CHARGE_PUMP_NET, manifest, cfg)
    assert "1m" in out  # zero ohm
    assert "benchgate: DNP omitted" in out
    if lib.exists():
        assert "NE555" in out
        assert "U1 __U1" not in out
