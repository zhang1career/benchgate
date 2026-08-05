"""Schematic coverage vs blocks.yaml / manifest spec."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from benchgate.kicad.project import KiCadProject, symbol_key
from benchgate.pipeline.local_blocks import load_blocks_config
from benchgate.schemas import MappingManifest


_PASSIVE_LIB_RE = re.compile(
    r"^(Device:[RCL]|Device:LED|power:|Mechanical:|Graphic:|Simulation_SPICE:)"
)


def _is_coverage_candidate(meta: dict[str, str]) -> bool:
    lib_id = meta.get("lib_id") or ""
    if _PASSIVE_LIB_RE.match(lib_id):
        return False
    if lib_id.startswith("Connector:"):
        return False
    return True


def schematic_references(project: KiCadProject) -> dict[str, dict[str, str]]:
    refs: dict[str, dict[str, str]] = {}
    for sym in project.iter_symbols():
        if not sym.reference or sym.in_bom is False:
            continue
        refs[sym.reference] = {
            "kicad_key": symbol_key(sym),
            "lib_id": sym.lib_id,
            "value": sym.value or "",
        }
    return refs


def coverage_report(
    *,
    design_dir: Path,
    manifest: MappingManifest,
    blocks_yaml: Path,
) -> dict[str, Any]:
    """List schematic references that have no block spec in blocks.yaml."""
    project = KiCadProject.load(design_dir)
    sch_refs = schematic_references(project)
    _, block_defs = load_blocks_config(blocks_yaml)

    spec_refs: set[str] = set()
    spec_keys: set[str] = set()
    for block in block_defs:
        if block.get("reference"):
            spec_refs.add(str(block["reference"]))
        if block.get("kicad_key"):
            spec_keys.add(str(block["kicad_key"]))

    manifest_spec_refs: set[str] = set()
    for entry in manifest.entries:
        if entry.spec and entry.reference:
            manifest_spec_refs.add(entry.reference)

    uncovered: list[dict[str, str]] = []
    for ref, meta in sorted(sch_refs.items()):
        if ref.startswith("#"):
            continue
        if not _is_coverage_candidate(meta):
            continue
        if ref in spec_refs or meta["kicad_key"] in spec_keys:
            continue
        if ref in manifest_spec_refs:
            continue
        uncovered.append({"reference": ref, **meta})

    return {
        "schematic_references": len(sch_refs),
        "blocks_with_spec": len(spec_refs),
        "uncovered_references": uncovered,
        "uncovered_count": len(uncovered),
    }
