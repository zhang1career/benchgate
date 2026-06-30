"""Transform KiCad SPICE netlist and inject ngspice models from manifest."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from benchgate.io.manifest import load_manifest
from benchgate.schemas import MappingManifest, SpiceModelKind

_GATE = "/Gate_Drive/"
_REFRESH_BUS = f"{_GATE}BST_REF"
_SHARED_NET = f"{_GATE}_NO_NET_"


def _fix_ams1117_pins(text: str) -> str:
    return text.replace(
        "XU5 GND +3V3 VIN_PORT AMS1117_3V3",
        "XU5 VIN_PORT +3V3 GND AMS1117_3V3",
    )


def _name_refresh_bus(text: str) -> str:
    """D2/D3 + R95/R96 share an unnamed net; label it BST_REF (HS refresh, not bootstrap charge)."""
    replacements = [
        (f"D3 {_GATE}SW_1 {_SHARED_NET}", f"D3 {_GATE}SW_1 {_REFRESH_BUS}"),
        (f"D2 {_GATE}SW_2 {_SHARED_NET}", f"D2 {_GATE}SW_2 {_REFRESH_BUS}"),
        (f"D3 {_GATE}SW_IN {_SHARED_NET}", f"D3 {_GATE}SW_IN {_REFRESH_BUS}"),
        (f"D2 {_GATE}SW_OUT {_SHARED_NET}", f"D2 {_GATE}SW_OUT {_REFRESH_BUS}"),
        (f"R95 {_GATE}BST_REFRESH {_SHARED_NET}", f"R95 {_GATE}BST_REFRESH {_REFRESH_BUS}"),
        (f"R96 GND {_SHARED_NET}", f"R96 GND {_REFRESH_BUS}"),
        (f"R97 {_SHARED_NET} {_SHARED_NET}", "* benchgate: R97 removed (shorted refresh net)"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _split_legacy_merged_drivers(text: str) -> str:
    """
    Older KiCad exports tied U2/U3 HB/HO/LO to one net.
    Split per-driver nets; keep D2/D3 on the HS refresh bus (not on BST).
    """
    replacements = [
        (
            f"XU2 VIN_PORT {_SHARED_NET} {_SHARED_NET} {_GATE}SW_IN "
            f"{_GATE}PWM_HIN1 {_GATE}PWM_LIN1 GND {_SHARED_NET} UCC27211",
            f"XU2 VIN_PORT {_GATE}BST_1 {_GATE}HO1 {_GATE}SW_IN "
            f"{_GATE}PWM_HIN1 {_GATE}PWM_LIN1 GND {_GATE}LO1 UCC27211",
        ),
        (
            f"XU3 VIN_PORT {_SHARED_NET} {_SHARED_NET} {_GATE}SW_OUT "
            f"{_GATE}PWM_HIN2 {_GATE}PWM_LIN2 GND {_SHARED_NET} UCC27211",
            f"XU3 VIN_PORT {_GATE}BST_2 {_GATE}HO2 {_GATE}SW_OUT "
            f"{_GATE}PWM_HIN2 {_GATE}PWM_LIN2 GND {_GATE}LO2 UCC27211",
        ),
        (f"R91 {_SHARED_NET} {_GATE}Q1_GATE", f"R91 {_GATE}HO1 {_GATE}Q1_GATE"),
        (f"R92 {_SHARED_NET} {_GATE}Q2_GATE", f"R92 {_GATE}LO1 {_GATE}Q2_GATE"),
        (f"R93 {_SHARED_NET} {_GATE}Q3_GATE", f"R93 {_GATE}HO2 {_GATE}Q3_GATE"),
        (f"R94 {_SHARED_NET} {_GATE}Q4_GATE", f"R94 {_GATE}LO2 {_GATE}Q4_GATE"),
        (f"C81 {_SHARED_NET} {_GATE}SW_IN", f"C81 {_GATE}BST_1 {_GATE}SW_IN"),
        (f"C82 {_SHARED_NET} {_GATE}SW_OUT", f"C82 {_GATE}BST_2 {_GATE}SW_OUT"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def split_gate_drive_nets(netlist_text: str) -> str:
    """
    Normalize gate-drive SPICE netlists for ngspice.

    - Bootstrap: UCC27211 internal diode + C81/C82 on BST_* / SW_*
    - D2/D3: HS refresh assist on BST_REF (not bootstrap charge path)
    - Legacy: split merged HB/HO/LO when KiCad exported a single _NO_NET_
    """
    if "UCC27211" not in netlist_text:
        return _fix_ams1117_pins(netlist_text)

    text = netlist_text
    if f"XU2 VIN_PORT {_SHARED_NET}" in text:
        text = _split_legacy_merged_drivers(text)
    text = _name_refresh_bus(text)
    return _fix_ams1117_pins(text)


_VOUT = "/H-Bridge_Power/VOUT"
_ISENSE_RAW = "/H-Bridge_Power/ISENSE_RAW"
_ADC_IOUT = "/Sense_&_Control/ADC_IOUT"


def format_rload(rload_ohm: float) -> str:
    """Load resistor between VOUT and ISENSE_RAW (J2 differential output)."""
    return f"RLOAD {_VOUT} {_ISENSE_RAW} {rload_ohm:g}"


def inject_isense_path(
    netlist_text: str,
    *,
    ina180_lib: Path | None = None,
    rload_ohm: float = 10.0,
) -> str:
    """
    KiCad marks U4 (INA180A3) exclude_from_sim; inject ADC_IOUT for ngspice.

    Schematic: J2+ = VOUT, J2- = ISENSE_RAW, R51 = ISENSE_RAW → GND.
    Load is RLOAD across VOUT–ISENSE_RAW. At DC, I(R51) = (VOUT−ISENSE)/Rload.

    INA180 is emulated from the J2 differential (Iload); this matches the shunt at DC
    and avoids switch-node ripple that dominates V(ISENSE_RAW)−GND in tran sim.
    """
    if "benchgate ISENSE path" in netlist_text:
        return netlist_text
    if re.search(r"^Eiout\s", netlist_text, flags=re.MULTILINE | re.IGNORECASE):
        return netlist_text
    if _ADC_IOUT not in netlist_text or _ISENSE_RAW not in netlist_text:
        return netlist_text
    if _VOUT not in netlist_text:
        return netlist_text

    scale = 1.0 / rload_ohm
    lines: list[str] = [
        "* --- benchgate ISENSE path (Iload from J2 VOUT–ISENSE_RAW) ---",
        f"Eiout {_ADC_IOUT} GND VALUE={{min(max({scale}*(V({_VOUT})-V({_ISENSE_RAW})), 0), 3.2)}}",
    ]
    block = "\n".join(lines) + "\n"
    if re.search(r"^\.control\b", netlist_text, flags=re.MULTILINE | re.IGNORECASE):
        return re.sub(
            r"^(\.control\b)",
            block + r"\1",
            netlist_text,
            count=1,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    if re.search(r"^\.end\s*$", netlist_text, flags=re.MULTILINE | re.IGNORECASE):
        return re.sub(
            r"^(\.end\s*)$",
            block + r"\1",
            netlist_text,
            count=1,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    return netlist_text.rstrip() + "\n" + block


def build_include_block(manifest: MappingManifest) -> str:
    lines: list[str] = ["* --- benchgate auto-generated model includes ---"]
    seen: set[Path] = set()

    for entry in manifest.entries:
        if not entry.is_ready:
            continue
        if entry.spice_kind in (SpiceModelKind.SUBCKT, SpiceModelKind.TABLE) and entry.sim_library:
            path = entry.sim_library.resolve()
            if path not in seen:
                lines.append(f'.include "{path}"')
                seen.add(path)
    lines.append("* --- end benchgate includes ---")
    return "\n".join(lines) + "\n"


def load_sim_profile(config_path: Path, profile: str = "default") -> str:
    if not config_path.exists():
        return ""
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    block = data.get(profile) or data.get("default") or {}
    directives = block.get("directives", [])
    if not directives:
        return ""
    return "\n".join(str(d) for d in directives) + "\n"


def inject_models(
    netlist_text: str,
    manifest: MappingManifest,
    *,
    sim_profile_path: Path | None = None,
    profile: str = "default",
) -> str:
    if "benchgate auto-generated model includes" in netlist_text:
        text = netlist_text
    elif any(line.strip().lower().startswith(".include") for line in netlist_text.splitlines()):
        # KiCad already exported model libraries; avoid duplicate .include blocks.
        text = netlist_text
    else:
        text = build_include_block(manifest) + netlist_text

    profile_block = load_sim_profile(sim_profile_path, profile) if sim_profile_path else ""
    if profile_block and profile_block.strip() not in text:
        if re.search(r"^\.end\s*$", text, flags=re.MULTILINE | re.IGNORECASE):
            text = re.sub(
                r"^\.end\s*$",
                profile_block.rstrip() + "\n.end",
                text,
                count=1,
                flags=re.MULTILINE | re.IGNORECASE,
            )
        else:
            text = text.rstrip() + "\n" + profile_block + ".end\n"
    return text


def prepare_netlist(
    netlist_path: Path,
    manifest_path: Path,
    output_path: Path,
    *,
    sim_profile_path: Path | None = None,
    profile: str = "default",
) -> Path:
    text = netlist_path.read_text(encoding="utf-8", errors="replace")
    text = split_gate_drive_nets(text)
    manifest = load_manifest(manifest_path)
    text = inject_models(text, manifest, sim_profile_path=sim_profile_path, profile=profile)
    text = inject_isense_path(text)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return output_path
