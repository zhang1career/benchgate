"""Transform KiCad SPICE netlist and inject ngspice models from manifest."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from benchgate.io.manifest import load_manifest
from benchgate.schemas import MappingManifest, SpiceModelKind

def _fix_zero_ohm_links(text: str) -> str:
    """Replace 0 Ω schematic jumpers with 1 mΩ so ngspice avoids 1e-12 Ω warnings."""
    return re.sub(
        r"^(R\d+\s+\S+\s+\S+)\s+0\s*$",
        r"\1 1m",
        text,
        flags=re.MULTILINE,
    )


_BJT_LINE_RE = re.compile(r"^Q(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$", re.MULTILINE)
BJT_SPICE_ORDER = ("C", "B", "E")
FET_SPICE_ORDER = ("D", "G", "S")


def parse_sim_pins(sim_pins: str) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for part in sim_pins.split():
        if "=" not in part:
            continue
        num, term = part.split("=", 1)
        mapping[int(num)] = term.strip().upper()
    return mapping


def _spice_terminal_order(terms: set[str]) -> tuple[str, ...] | None:
    if {"C", "B", "E"}.issubset(terms):
        return BJT_SPICE_ORDER
    if {"D", "G", "S"}.issubset(terms):
        return FET_SPICE_ORDER
    return None


def fix_device_pin_order(text: str, manifest: MappingManifest) -> str:
    """Reorder Q/J lines from KiCad pin 1-2-3 export to SPICE C-B-E / D-G-S."""
    entries_by_ref = {
        e.reference: e for e in manifest.entries if e.reference and e.sim_pins
    }
    out_lines: list[str] = []

    for line in text.splitlines():
        match = _BJT_LINE_RE.match(line)
        if not match:
            out_lines.append(line)
            continue

        ref_suffix, n1, n2, n3, model = match.groups()
        ref = f"Q{ref_suffix}"
        entry = entries_by_ref.get(ref)
        if not entry or not entry.sim_pins:
            out_lines.append(line)
            continue

        pin_map = parse_sim_pins(entry.sim_pins)
        nodes_by_pin = {1: n1, 2: n2, 3: n3}
        term_nodes = {term: nodes_by_pin[pin] for pin, term in pin_map.items() if pin in nodes_by_pin}
        order = _spice_terminal_order(set(term_nodes))
        if not order or any(term_nodes.get(t) is None for t in order):
            out_lines.append(line)
            continue

        new_nodes = [term_nodes[t] for t in order]
        if new_nodes == [n1, n2, n3]:
            out_lines.append(line)
            continue

        out_lines.append(f"* benchgate: BJT pin order fixed for {ref}")
        out_lines.append(f"Q{ref_suffix} {' '.join(new_nodes)} {model}")

    return "\n".join(out_lines) + ("\n" if text.endswith("\n") else "")


def inject_save_directives(text: str, save_list: list[str]) -> str:
    """Inject ngspice output probes: ``.save`` dot cards for @dev[...], control ``save`` for the rest."""
    if not save_list:
        return text

    dot_items: list[str] = []
    ctrl_items: list[str] = []
    for item in save_list:
        token = item.strip()
        if token.startswith("@"):
            # Normalize legacy @q1[c] → @q1[ic] for ngspice .save dot cards.
            if token.endswith("[c]"):
                token = token[:-3] + "[ic]"
            dot_items.append(token)
        else:
            ctrl_items.append(token)

    if dot_items:
        dot_line = ".save all " + " ".join(dot_items)
        if dot_line.lower() not in text.lower():
            if re.search(r"^\.control\b", text, flags=re.MULTILINE | re.IGNORECASE):
                text = re.sub(
                    r"(?m)^(\.control\b)",
                    dot_line + "\n\\1",
                    text,
                    count=1,
                    flags=re.MULTILINE | re.IGNORECASE,
                )
            elif re.search(r"^\.end\s*$", text, flags=re.MULTILINE | re.IGNORECASE):
                text = re.sub(
                    r"(?m)^(\.end\s*)$",
                    dot_line + "\n\\1",
                    text,
                    count=1,
                    flags=re.MULTILINE | re.IGNORECASE,
                )
            else:
                text = text.rstrip() + "\n" + dot_line + "\n"

        # Leave ``write sim.raw all`` unchanged — listing @ probes after ``all`` narrows output.
        if re.search(r"(?m)^write\s+sim\.raw\s+all\s*$", text, re.IGNORECASE):
            pass
        elif re.search(r"(?m)^write\s+sim\.raw\b", text, re.IGNORECASE):
            extra = " ".join(dot_items)
            text = re.sub(
                r"(?m)^write\s+sim\.raw\s+.*$",
                f"write sim.raw all {extra}".rstrip(),
                text,
                flags=re.MULTILINE | re.IGNORECASE,
            )

    if ctrl_items and re.search(r"^\.control\b", text, flags=re.MULTILINE | re.IGNORECASE):
        save_cmd = "save " + " ".join(ctrl_items)
        if save_cmd.lower() not in text.lower():

            def _insert_run(match: re.Match[str]) -> str:
                return f"{save_cmd}\n{match.group(0)}"

            text, _ = re.subn(
                r"(?m)^(\s*run\s*)$",
                _insert_run,
                text,
                count=1,
            )
    return text


_PLACEHOLDER_RE = re.compile(r"^(\S+)\s+__\1\b.*$", re.MULTILINE)


def strip_unmodeled_placeholders(netlist_text: str) -> str:
    """Comment out KiCad placeholder elements for symbols without a SPICE model.

    KiCad exports a symbol that has no simulation model (connectors, test points,
    mounting holes, …) as a bare ``REF __REF`` line. ngspice then tries to parse it
    as a device (e.g. ``J3`` → JFET) and aborts with 'Unable to find definition of
    model'. These parts are not part of the electrical model, so neutralize them.
    """
    return _PLACEHOLDER_RE.sub(r"* benchgate: dropped unmodeled placeholder '\g<0>'", netlist_text)


def apply_netlist_rewrites(netlist_text: str, rules: list[tuple[str, str, tuple[str, ...]]]) -> str:
    """Literal find → replace, in order, from a profile's ``netlist_rewrite``.

    ``when`` substrings are checked against the netlist as it was before the first rule.
    """
    original = netlist_text
    for find, replace, when in rules:
        if all(w in original for w in when):
            netlist_text = netlist_text.replace(find, replace)
    return netlist_text


def append_netlist_lines(netlist_text: str, lines: list[str], when: tuple[str, ...] = ()) -> str:
    """Insert a profile's ``netlist_append`` lines before ``.control`` (else ``.end``)."""
    if not lines or not all(w in netlist_text for w in when):
        return netlist_text
    block = "\n".join(lines) + "\n"
    if block in netlist_text:
        return netlist_text
    for pattern in (r"^(\.control\b)", r"^(\.end\s*)$"):
        if re.search(pattern, netlist_text, flags=re.MULTILINE | re.IGNORECASE):
            return re.sub(
                pattern,
                lambda m: block + m.group(1),
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


def load_profile_excludes(config_path: Path, profile: str = "default") -> list[str]:
    """Read a profile's ``exclude`` list: regexes of netlist lines to drop before sim."""
    if not config_path.exists():
        return []
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    block = data.get(profile) or {}
    patterns = block.get("exclude", [])
    return [str(p) for p in patterns] if patterns else []


def strip_excluded_lines(netlist_text: str, patterns: list[str]) -> str:
    """Comment out element lines matching any exclude regex (e.g. isolate one block)."""
    if not patterns:
        return netlist_text
    regexes = [re.compile(p) for p in patterns]
    out: list[str] = []
    for line in netlist_text.splitlines():
        if line and not line.startswith("*") and any(r.search(line) for r in regexes):
            out.append("* benchgate: excluded -> " + line)
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def load_sim_profile(config_path: Path, profile: str = "default") -> str:
    if not config_path.exists():
        return ""
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    block = data.get(profile) or data.get("default") or {}
    directives = block.get("directives", [])
    if not directives:
        return ""
    return "\n".join(str(d) for d in directives) + "\n"


def merge_manifest_includes(netlist_text: str, manifest: MappingManifest) -> str:
    """Append ``.include`` lines for ready manifest models not already referenced."""
    text = netlist_text
    seen: set[str] = set()
    for line in text.splitlines():
        if line.strip().lower().startswith(".include"):
            seen.add(line.strip().lower())
    inserts: list[str] = []
    for entry in manifest.entries:
        if not entry.is_ready or not entry.sim_library:
            continue
        if entry.spice_kind not in (SpiceModelKind.SUBCKT, SpiceModelKind.TABLE):
            continue
        path = entry.sim_library.resolve()
        inc = f'.include "{path}"'
        if inc.lower() in seen or inc.lower() in text.lower():
            continue
        inserts.append(inc)
        seen.add(inc.lower())
    if not inserts:
        return text
    block = "* --- benchgate supplemental model includes ---\n" + "\n".join(inserts) + "\n"
    lines = text.splitlines()
    insert_at = 0
    for i, line in enumerate(lines):
        if line.strip().lower().startswith(".title"):
            insert_at = i + 1
            break
    lines[insert_at:insert_at] = block.rstrip("\n").splitlines()
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def inject_models(
    netlist_text: str,
    manifest: MappingManifest,
    *,
    sim_profile_path: Path | None = None,
    profile: str = "default",
    save_list: list[str] | None = None,
) -> str:
    if "benchgate auto-generated model includes" in netlist_text:
        text = netlist_text
    elif any(line.strip().lower().startswith(".include") for line in netlist_text.splitlines()):
        text = merge_manifest_includes(netlist_text, manifest)
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

    if save_list:
        text = inject_save_directives(text, save_list)
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
    manifest = load_manifest(manifest_path)
    from benchgate.sim.netlist_fixup import apply_netlist_fixups, load_fixup_config
    from benchgate.sim.profile import load_profile_append, load_profile_rewrites, load_profile_save

    fixup_cfg = load_fixup_config(sim_profile_path, profile) if sim_profile_path else None
    text = apply_netlist_fixups(text, manifest, fixup_cfg)
    if sim_profile_path:
        text = strip_excluded_lines(text, load_profile_excludes(sim_profile_path, profile))
        text = apply_netlist_rewrites(text, load_profile_rewrites(sim_profile_path, profile))

    save_list = load_profile_save(sim_profile_path, profile) if sim_profile_path else []
    text = inject_models(
        text,
        manifest,
        sim_profile_path=sim_profile_path,
        profile=profile,
        save_list=save_list,
    )
    if sim_profile_path:
        lines, when = load_profile_append(sim_profile_path, profile)
        text = append_netlist_lines(text, lines, when)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return output_path
