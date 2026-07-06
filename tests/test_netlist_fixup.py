"""Tests for gate-drive netlist rewrite."""

from benchgate.schemas import ComponentMapping, MappingManifest, SpiceModelKind
from benchgate.sim.netlist import (
    _fix_zero_ohm_links,
    fix_device_pin_order,
    format_rload,
    inject_isense_path,
    inject_save_directives,
    split_gate_drive_nets,
)

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


def test_split_legacy_merged_drivers() -> None:
    out = split_gate_drive_nets(LEGACY)
    assert "/Gate_Drive/BST_1" in out
    assert "/Gate_Drive/HO1" in out
    assert "/Gate_Drive/LO1" in out
    assert "/Gate_Drive/BST_2" in out
    assert "D3 /Gate_Drive/SW_IN /Gate_Drive/BST_REF" in out
    assert "D2 /Gate_Drive/SW_OUT /Gate_Drive/BST_REF" in out
    assert "C81 /Gate_Drive/BST_1 /Gate_Drive/SW_IN" in out
    assert "/Gate_Drive/_NO_NET_" not in out
    assert "R91 /Gate_Drive/HO1 /Gate_Drive/Q1_GATE" in out


def test_name_refresh_bus_on_labeled_schematic() -> None:
    out = split_gate_drive_nets(LABELED)
    assert "D3 /Gate_Drive/SW_1 /Gate_Drive/BST_REF" in out
    assert "D2 /Gate_Drive/SW_2 /Gate_Drive/BST_REF" in out
    assert "R95 /Gate_Drive/BST_REFRESH /Gate_Drive/BST_REF" in out
    assert "R96 GND /Gate_Drive/BST_REF" in out
    assert "/Gate_Drive/_NO_NET_" not in out
    assert "/Gate_Drive/BST_1" in out
    assert "benchgate: R97 removed" in out


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


def test_fix_ams1117_pin_order() -> None:
    out = split_gate_drive_nets("XU5 GND +3V3 VIN_PORT AMS1117_3V3\n")
    assert "XU5 VIN_PORT +3V3 GND AMS1117_3V3" in out


def test_format_rload() -> None:
    assert format_rload(10) == "RLOAD /H-Bridge_Power/VOUT /H-Bridge_Power/ISENSE_RAW 10"


def test_inject_isense_path() -> None:
    netlist = (
        ".title test\n"
        "R51 /H-Bridge_Power/ISENSE_RAW GND 10m\n"
        "RLOAD /H-Bridge_Power/VOUT /H-Bridge_Power/ISENSE_RAW 10\n"
        "C70 /Sense_&_Control/ADC_IOUT GND 0.1u\n"
    )
    out = inject_isense_path(netlist)
    assert "benchgate ISENSE path" in out
    assert "Eiout /Sense_&_Control/ADC_IOUT GND VALUE={min(max(0.1*(V(/H-Bridge_Power/VOUT)-V(/H-Bridge_Power/ISENSE_RAW)), 0), 3.2)}" in out
