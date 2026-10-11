"""Netlist fixups and profile-driven netlist rewrites."""

from pathlib import Path

from benchgate.cosim.runner import format_rload
from benchgate.io.manifest import save_manifest
from benchgate.schemas import ComponentMapping, MappingManifest, SpiceModelKind
from benchgate.sim.netlist import (
    _fix_zero_ohm_links,
    append_netlist_lines,
    apply_netlist_rewrites,
    fix_device_pin_order,
    inject_save_directives,
    prepare_netlist,
)

G = "/Gate_Drive/"
S = G + "_NO_NET_"
LEGACY_WHEN = (f"XU2 VIN_PORT {S}", "UCC27211")
RULES = [
    (f"R91 {S} {G}Q1_GATE", f"R91 {G}HO1 {G}Q1_GATE", LEGACY_WHEN),
    (f"C81 {S} {G}SW_IN", f"C81 {G}BST_1 {G}SW_IN", LEGACY_WHEN),
    (f"D3 {G}SW_IN {S}", f"D3 {G}SW_IN {G}BST_REF", ("UCC27211",)),
    (f"D3 {G}SW_1 {S}", f"D3 {G}SW_1 {G}BST_REF", ("UCC27211",)),
    ("XU5 GND +3V3 VIN_PORT AMS1117_3V3", "XU5 VIN_PORT +3V3 GND AMS1117_3V3", ()),
]

LEGACY = """
XU2 VIN_PORT /Gate_Drive/_NO_NET_ /Gate_Drive/_NO_NET_ /Gate_Drive/SW_IN /Gate_Drive/PWM_HIN1 /Gate_Drive/PWM_LIN1 GND /Gate_Drive/_NO_NET_ UCC27211
R91 /Gate_Drive/_NO_NET_ /Gate_Drive/Q1_GATE 10
R92 /Gate_Drive/_NO_NET_ /Gate_Drive/Q2_GATE 10
C81 /Gate_Drive/_NO_NET_ /Gate_Drive/SW_IN 0.047u
XU3 VIN_PORT /Gate_Drive/_NO_NET_ /Gate_Drive/_NO_NET_ /Gate_Drive/SW_OUT /Gate_Drive/PWM_HIN2 /Gate_Drive/PWM_LIN2 GND /Gate_Drive/_NO_NET_ UCC27211
R93 /Gate_Drive/_NO_NET_ /Gate_Drive/Q3_GATE 10
R94 /Gate_Drive/_NO_NET_ /Gate_Drive/Q4_GATE 10
C82 /Gate_Drive/_NO_NET_ /Gate_Drive/SW_OUT 0.047u
D3 /Gate_Drive/SW_IN /Gate_Drive/_NO_NET_ SS34
D2 /Gate_Drive/SW_OUT /Gate_Drive/_NO_NET_ SS34
R95 /Gate_Drive/BST_REFRESH /Gate_Drive/_NO_NET_ 100
R96 GND /Gate_Drive/_NO_NET_ 10k
R97 /Gate_Drive/_NO_NET_ /Gate_Drive/_NO_NET_ 10
"""

LABELED = """
C81 /Gate_Drive/BST_1 /Gate_Drive/SW_1 0.047u
D3 /Gate_Drive/SW_1 /Gate_Drive/_NO_NET_ SS34
D2 /Gate_Drive/SW_2 /Gate_Drive/_NO_NET_ SS34
R95 /Gate_Drive/BST_REFRESH /Gate_Drive/_NO_NET_ 100
R96 GND /Gate_Drive/_NO_NET_ 10k
R97 /Gate_Drive/_NO_NET_ /Gate_Drive/_NO_NET_ 10
XU2 VIN_PORT /Gate_Drive/BST_1 /Gate_Drive/Q1_GATE_RAW /Gate_Drive/SW_1 /Gate_Drive/PWM_HIN1 /Gate_Drive/PWM_LIN1 GND /Gate_Drive/Q2_GATE_RAW UCC27211
XU3 VIN_PORT /Gate_Drive/BST_2 /Gate_Drive/Q3_GATE_RAW /Gate_Drive/SW_2 /Gate_Drive/PWM_HIN2 /Gate_Drive/PWM_LIN2 GND /Gate_Drive/Q4_GATE_RAW UCC27211
"""


def test_rewrite_rules_respect_when() -> None:
    legacy = apply_netlist_rewrites(LEGACY, RULES)
    assert "R91 /Gate_Drive/HO1 /Gate_Drive/Q1_GATE" in legacy
    assert "C81 /Gate_Drive/BST_1 /Gate_Drive/SW_IN" in legacy
    assert "D3 /Gate_Drive/SW_IN /Gate_Drive/BST_REF" in legacy

    labeled = apply_netlist_rewrites(LABELED + f"R91 {S} {G}Q1_GATE 10\n", RULES)
    assert f"R91 {S} {G}Q1_GATE" in labeled
    assert "D3 /Gate_Drive/SW_1 /Gate_Drive/BST_REF" in labeled


def test_board_specific_lines_untouched_without_profile_rules(tmp_path: Path) -> None:
    netlist = tmp_path / "board.net"
    netlist.write_text(".title t\n" + LEGACY + "XU5 GND +3V3 VIN_PORT AMS1117_3V3\n.end\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    save_manifest(MappingManifest(), manifest)
    profiles = tmp_path / "sim_profiles.yaml"
    profiles.write_text("default:\n  directives: ['.control', 'op', '.endc']\n", encoding="utf-8")
    out = prepare_netlist(netlist, manifest, tmp_path / "out.cir", sim_profile_path=profiles).read_text()
    assert f"R91 {S} {G}Q1_GATE" in out
    assert "XU5 GND +3V3 VIN_PORT AMS1117_3V3" in out
    assert f"D3 {G}SW_IN {S}" in out


def test_profile_rewrite_and_append_in_prepare_netlist(tmp_path: Path) -> None:
    netlist = tmp_path / "board.net"
    netlist.write_text(".title t\nXU5 GND +3V3 VIN_PORT AMS1117_3V3\nR1 A B 1k\n.end\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    save_manifest(MappingManifest(), manifest)
    profiles = tmp_path / "sim_profiles.yaml"
    profiles.write_text(
        "board:\n"
        "  directives: ['.control', 'op', '.endc']\n"
        "  netlist_rewrite:\n"
        "    - {find: 'XU5 GND +3V3 VIN_PORT', replace: 'XU5 VIN_PORT +3V3 GND'}\n"
        "  netlist_append:\n"
        "    when: ['R1 A B']\n"
        "    lines: ['Eprobe P 0 VALUE={V(A)-V(B)}']\n",
        encoding="utf-8",
    )
    out = prepare_netlist(netlist, manifest, tmp_path / "out.cir", sim_profile_path=profiles, profile="board").read_text()
    assert "XU5 VIN_PORT +3V3 GND AMS1117_3V3" in out
    assert out.index("Eprobe") < out.index(".control")


def test_append_lines_when_and_idempotent() -> None:
    lines = ["Eiout OUT 0 VALUE={V(A)}"]
    netlist = ".title t\nR1 A 0 1k\n.control\nop\n.endc\n.end\n"
    assert append_netlist_lines(netlist, lines, ("MISSING",)) == netlist
    once = append_netlist_lines(netlist, lines, ("R1 A",))
    assert once.index("Eiout") < once.index(".control")
    assert append_netlist_lines(once, lines) == once


def test_fix_zero_ohm_links() -> None:
    netlist = "R5 Net-_C2-Pad1_ Net-_U1-THRES_ 0\nR6 Net-_C2-Pad1_ Net-_U1-TRIG_ 0\n"
    out = _fix_zero_ohm_links(netlist)
    assert "R5 Net-_C2-Pad1_ Net-_U1-THRES_ 1m" in out
    assert "R6 Net-_C2-Pad1_ Net-_U1-TRIG_ 1m" in out


def test_fix_bjt_pin_order() -> None:
    netlist = "Q1 Net-_Q1-B_ Net-_Q1-E_ +12V SS8050\n"
    manifest = MappingManifest(
        entries=[
            ComponentMapping(
                kicad_key="Transistor_BJT:SS8050::SS8050",
                reference="Q1",
                spice_kind=SpiceModelKind.SUBCKT,
                sim_pins="1=B 2=E 3=C",
            )
        ]
    )
    out = fix_device_pin_order(netlist, manifest)
    assert "Q1 +12V Net-_Q1-B_ Net-_Q1-E_ SS8050" in out
    assert "BJT pin order fixed" in out


def test_inject_save_directives() -> None:
    netlist = ".control\ntran 1u 10m\nrun\nwrite sim.raw all\n.endc\n.end\n"
    out = inject_save_directives(netlist, ["@q1[c]", "v(out)"])
    assert ".save all @q1[ic]" in out
    assert "save v(out)" in out
    assert "write sim.raw all" in out
    assert "write sim.raw all @q1" not in out
    assert out.index(".save") < out.index(".control")


def test_format_rload() -> None:
    assert format_rload(10) == "RLOAD /H-Bridge_Power/VOUT /H-Bridge_Power/ISENSE_RAW 10"
