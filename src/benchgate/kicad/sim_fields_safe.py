"""KiCad 10-safe Sim.* writer — text edit only, no kicad-tools Schematic.save()."""

from __future__ import annotations

import re
from pathlib import Path

from benchgate.kicad.spice_fields import SimFields

_REF_PROPERTY_RE = re.compile(r'^\t\t\(property "Reference" "([^"]+)"', re.MULTILINE)
_SIM_LIBRARY_RE = re.compile(r'^\t\t\(property "Sim\.Library"', re.MULTILINE)


def _property_block(name: str, value: str, at: str = "0 0 0") -> str:
    return (
        f'\t\t(property "{name}" "{value}"\n'
        f"\t\t\t(at {at} 0)\n"
        f"\t\t\t(hide yes)\n"
        f"\t\t\t(show_name no)\n"
        f"\t\t\t(do_not_autoplace no)\n"
        f"\t\t\t(effects\n"
        f"\t\t\t\t(font\n"
        f"\t\t\t\t\t(size 1.27 1.27)\n"
        f"\t\t\t\t)\n"
        f"\t\t\t)\n"
        f"\t\t)\n"
    )


def _symbol_insertion_point(text: str, reference: str) -> int | None:
    """Return byte index to insert Sim.* properties (before pin/instances)."""
    ref_match = _REF_PROPERTY_RE.search(text)
    while ref_match:
        if ref_match.group(1) == reference:
            start = ref_match.start()
            # Walk back to the enclosing (symbol block at two tabs.
            sym_start = text.rfind("\n\t(symbol", 0, start)
            if sym_start < 0:
                sym_start = text.find("\t(symbol", 0, start)
            if sym_start < 0:
                return None
            block = text[sym_start:]
            for marker in ("\n\t\t(pin ", "\n\t\t(instances"):
                idx = block.find(marker)
                if idx >= 0:
                    return sym_start + idx + 1
            return sym_start + len(block)
        ref_match = _REF_PROPERTY_RE.search(text, ref_match.end())
    return None


def read_sim_fields_safe(schematic_path: Path, reference: str) -> SimFields | None:
    text = schematic_path.read_text(encoding="utf-8")
    sym_start = text.find(f'(property "Reference" "{reference}"')
    if sym_start < 0:
        return None
    tail = text[sym_start : sym_start + 4000]

    def _extract(prop: str) -> str:
        m = re.search(rf'\(property "{re.escape(prop)}" "([^"]*)"', tail)
        return m.group(1) if m else ""

    return SimFields(library=_extract("Sim.Library"), name=_extract("Sim.Name"), pins=_extract("Sim.Pins"))


def apply_sim_fields_safe(
    schematic_path: Path,
    reference: str,
    *,
    sim_library: str | Path,
    sim_name: str,
    sim_pins: str = "",
) -> bool:
    """Insert or update Sim.* properties for ``reference`` without rewriting the whole sch."""
    path = schematic_path.resolve()
    text = path.read_text(encoding="utf-8")
    library = str(sim_library)

    if read_sim_fields_safe(path, reference) and f'(property "Sim.Name" "{sim_name}"' in text:
        return False

    # Remove existing Sim.* in the symbol block when updating.
    insert_at = _symbol_insertion_point(text, reference)
    if insert_at is None:
        raise KeyError(f'Reference {reference!r} not found in {path}')

    block_end = text.find("\n\t(symbol", insert_at)
    if block_end < 0:
        block_end = len(text)
    head = text[:insert_at]
    block = text[insert_at:block_end]
    tail = text[block_end:]
    block = re.sub(r'\t\t\(property "Sim\.[^"]+"[\s\S]*?\n\t\t\)\n', "", block)

    props = _property_block("Sim.Library", library)
    props += _property_block("Sim.Name", sim_name)
    if sim_pins:
        props += _property_block("Sim.Pins", sim_pins)

    new_text = head + props + block + tail
    path.write_text(new_text, encoding="utf-8")
    return True
