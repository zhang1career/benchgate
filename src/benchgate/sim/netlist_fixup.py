"""Profile-driven netlist fixups (zero-ohm, BJT pins, manifest subckt, DNP)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from benchgate.schemas import MappingManifest, SpiceModelKind
from benchgate.sim.netlist import (
    _fix_zero_ohm_links,
    fix_device_pin_order,
    parse_sim_pins,
    strip_unmodeled_placeholders,
)

_PLACEHOLDER_LINE_RE = re.compile(r"^(\S+)\s+__\1\b.*$")
_DNP_LINE_RE = re.compile(r"^(\S+\s+\S+\s+\S+)\s+DNP\s*$", re.IGNORECASE | re.MULTILINE)

_KICAD_PIN_SUFFIX: dict[str, tuple[str, ...]] = {
    "TRIG": ("TRIG",),
    "OUT": ("OUT",),
    "CONT": ("CONT",),
    "THR": ("THRES", "THR"),
    "DIS": ("DISCH", "DIS"),
    "RST": ("RST",),
    "CLK": ("CLK",),
    "EN": ("EN",),
}

_VCC_RAILS = ("VIN", "VOUT", "+12V", "+24V", "+5V", "VCC", "VDD")


@dataclass
class NetlistFixupConfig:
    zero_ohm: bool = True
    bjt_pins: bool = True
    inject_subckt: bool = True
    dnp: bool = True
    diode_models: bool = True
    strip_placeholders: bool = True

    @classmethod
    def from_profile(cls, block: dict[str, Any] | None) -> NetlistFixupConfig:
        if not block:
            return cls()
        raw = block.get("netlist_fixup")
        if raw is None:
            return cls()
        if isinstance(raw, list):
            disabled = {str(x).lower() for x in raw}
            return cls(
                zero_ohm="zero_ohm" not in disabled,
                bjt_pins="bjt_pins" not in disabled,
                inject_subckt="inject_subckt" not in disabled,
                dnp="dnp" not in disabled,
                diode_models="diode_models" not in disabled,
                strip_placeholders="strip_placeholders" not in disabled,
            )
        if isinstance(raw, dict):
            return cls(
                zero_ohm=bool(raw.get("zero_ohm", True)),
                bjt_pins=bool(raw.get("bjt_pins", True)),
                inject_subckt=bool(raw.get("inject_subckt", True)),
                dnp=bool(raw.get("dnp", True)),
                diode_models=bool(raw.get("diode_models", True)),
                strip_placeholders=bool(raw.get("strip_placeholders", True)),
            )
        return cls()


def load_fixup_config(sim_profile_path, profile: str = "default") -> NetlistFixupConfig:
    from benchgate.sim.profile import load_profile_block

    if not sim_profile_path:
        return NetlistFixupConfig()
    return NetlistFixupConfig.from_profile(load_profile_block(sim_profile_path, profile))


def _collect_nets(text: str) -> set[str]:
    nets: set[str] = set()
    for line in text.splitlines():
        for token in line.split():
            if token.startswith("Net-_") or token.startswith("/") or token.upper() in _VCC_RAILS:
                nets.add(token)
            elif token.upper() == "GND":
                nets.add("GND")
    return nets


def _net_belongs_to_ref(net: str, ref: str) -> bool:
    ru = ref.upper()
    nu = net.upper()
    return f"_{ru}-" in nu or nu.startswith(f"NET-_{ru}-")


def _find_terminal_net(text: str, ref: str, terminal: str, all_nets: set[str]) -> str | None:
    term = terminal.upper()
    if term == "GND":
        return "GND" if "GND" in all_nets or re.search(r"\bGND\b", text) else "0"
    if term == "VCC":
        for rail in _VCC_RAILS:
            if rail in all_nets:
                return rail
        return None
    if term == "RST":
        for net in all_nets:
            low = net.lower()
            if low.startswith("/rst") or net.upper().endswith("-RST_"):
                return net
        return None

    suffixes = _KICAD_PIN_SUFFIX.get(term, (term,))
    for net in sorted(all_nets):
        if not _net_belongs_to_ref(net, ref):
            continue
        net_upper = net.upper()
        for suf in suffixes:
            if f"-{suf}" in net_upper or net_upper.endswith(f"-{suf}_"):
                return net
    return None


def resolve_subckt_nets(text: str, ref: str, sim_pins: str) -> list[str | None]:
    pin_map = parse_sim_pins(sim_pins)
    ordered_terms = [pin_map[i] for i in sorted(pin_map.keys())]
    all_nets = _collect_nets(text)
    return [_find_terminal_net(text, ref, term, all_nets) for term in ordered_terms]


def inject_manifest_subckts(text: str, manifest: MappingManifest) -> str:
    """Replace ``REF __REF`` placeholders with ``XREF ... subckt`` when manifest is ready."""
    entries_by_ref = {
        e.reference: e
        for e in manifest.entries
        if e.reference and e.is_ready and e.sim_name and e.sim_pins
    }
    if not entries_by_ref:
        return text

    out_lines: list[str] = []
    for line in text.splitlines():
        match = _PLACEHOLDER_LINE_RE.match(line)
        if not match:
            out_lines.append(line)
            continue

        ref = match.group(1)
        entry = entries_by_ref.get(ref)
        if not entry or entry.spice_kind != SpiceModelKind.SUBCKT or not entry.sim_pins:
            out_lines.append(line)
            continue

        nets = resolve_subckt_nets(text, ref, entry.sim_pins)
        if any(n is None for n in nets):
            out_lines.append(line)
            continue

        xname = ref if ref.upper().startswith("X") else f"X{ref}"
        netlist = " ".join(nets)
        out_lines.append(f"* benchgate: injected subckt from manifest ({ref} missing Sim.* fields)")
        out_lines.append(f"{xname} {netlist} {entry.sim_name}")

    return "\n".join(out_lines) + ("\n" if text.endswith("\n") else "")


def strip_dnp_elements(text: str) -> str:
    """Comment out DNP (do-not-populate) passives so ngspice does not see ``DNP`` as a value."""
    return _DNP_LINE_RE.sub(r"* benchgate: DNP omitted -> \1", text)


def inject_diode_models(text: str, manifest: MappingManifest) -> str:
    """Replace KiCad ``.model __D1 D`` placeholders with manifest-bound diode models."""
    by_ref = {
        e.reference: e
        for e in manifest.entries
        if e.reference and e.reference.upper().startswith("D") and e.sim_name and e.is_ready
    }
    if not by_ref:
        return text

    out_lines: list[str] = []
    for line in text.splitlines():
        model_match = re.match(r"^\.model\s+__(D\d+)\s+D\s*$", line, re.IGNORECASE)
        if model_match:
            ref = model_match.group(1).upper()
            entry = by_ref.get(ref)
            if entry and entry.sim_name:
                out_lines.append(f"* benchgate: dropped KiCad placeholder .model __{ref} (use {entry.sim_name} from include)")
                continue
        dev_match = re.match(r"^(D\d+\s+\S+\s+\S+)\s+__(D\d+)\s*$", line, re.IGNORECASE)
        if dev_match:
            body, ref = dev_match.group(1), dev_match.group(2).upper()
            entry = by_ref.get(ref)
            if entry and entry.sim_name:
                out_lines.append(f"{body} {entry.sim_name}")
                continue
        out_lines.append(line)
    return "\n".join(out_lines) + ("\n" if text.endswith("\n") else "")


def apply_netlist_fixups(
    text: str,
    manifest: MappingManifest,
    config: NetlistFixupConfig | None = None,
) -> str:
    cfg = config or NetlistFixupConfig()
    if cfg.inject_subckt:
        text = inject_manifest_subckts(text, manifest)
    if cfg.diode_models:
        text = inject_diode_models(text, manifest)
    if cfg.dnp:
        text = strip_dnp_elements(text)
    if cfg.strip_placeholders:
        text = strip_unmodeled_placeholders(text)
    if cfg.zero_ohm:
        text = _fix_zero_ohm_links(text)
    if cfg.bjt_pins:
        text = fix_device_pin_order(text, manifest)
    return text
