"""Scan KiCad schematic → manifest.yaml."""

from __future__ import annotations

import re
from pathlib import Path

from benchgate.io.manifest import load_manifest, save_manifest
from benchgate.kicad.project import KiCadProject, iter_symbols, symbol_key
from benchgate.kicad.spice_fields import read_sim_fields
from benchgate.schemas import ComponentMapping, MappingManifest, SpiceModelKind

LIB_RULES: list[tuple[str, SpiceModelKind]] = [
    (r"^Device:R$", SpiceModelKind.PASSIVE),
    (r"^Device:C$", SpiceModelKind.PASSIVE),
    (r"^Device:L$", SpiceModelKind.PASSIVE),
    (r"^Device:D", SpiceModelKind.BUILTIN),
    (r"^Device:LED", SpiceModelKind.BUILTIN),
]


def infer_kind(lib_id: str) -> SpiceModelKind:
    for pattern, kind in LIB_RULES:
        if re.search(pattern, lib_id):
            return kind
    if ":" in lib_id:
        return SpiceModelKind.SUBCKT
    return SpiceModelKind.UNMAPPED


def sync_schematic_to_manifest(
    project: KiCadProject,
    manifest: MappingManifest,
    *,
    subckt_dir: Path,
) -> MappingManifest:
    sch = project.schematic_doc()
    for sym in iter_symbols(sch, project.root):
        if not sym.reference or sym.in_bom is False:
            continue
        key = symbol_key(sym)
        sim = read_sim_fields(sym)
        existing = manifest.find(key)
        entry = existing or ComponentMapping(kicad_key=key, reference=sym.reference)

        if sim.configured:
            lib_path = Path(sim.library)
            if not lib_path.is_absolute():
                lib_path = (project.root / sim.library).resolve()
            entry.spice_kind = SpiceModelKind.SUBCKT
            entry.sim_library = lib_path
            entry.sim_name = sim.name
            entry.sim_pins = sim.pins or None
        elif existing and existing.is_ready:
            pass
        else:
            kind = infer_kind(sym.lib_id)
            entry.spice_kind = kind
            if kind == SpiceModelKind.SUBCKT:
                safe = re.sub(r"[^\w\-]", "_", sym.value or sym.lib_id.split(":")[-1])
                candidate = subckt_dir / f"{safe}.lib"
                if candidate.exists():
                    entry.sim_library = candidate
                    entry.sim_name = safe.upper()

        entry.reference = sym.reference
        entry.metadata["lib_id"] = sym.lib_id
        entry.metadata["value"] = sym.value
        manifest.upsert(entry)
    return manifest


def mapping_status(manifest: MappingManifest) -> dict[str, list[str]]:
    ready, pending, unmapped = [], [], []
    for e in manifest.entries:
        bucket = {"ready": ready, "pending": pending, "unmapped": unmapped}[e.status]
        bucket.append(e.kicad_key)
    return {"ready": ready, "pending": pending, "unmapped": unmapped}


def apply_measured_model(
    manifest: MappingManifest,
    kicad_key: str,
    *,
    lib_path: Path,
    sim_name: str,
    sim_pins: str = "",
    measured_params: dict[str, float] | None = None,
) -> ComponentMapping:
    entry = manifest.find(kicad_key) or ComponentMapping(kicad_key=kicad_key)
    entry.spice_kind = SpiceModelKind.SUBCKT
    entry.sim_library = lib_path
    entry.sim_name = sim_name
    entry.sim_pins = sim_pins or None
    if measured_params and entry.measured:
        entry.measured.params.update(measured_params)
    manifest.upsert(entry)
    return entry


def sync_project(
    design_dir: Path,
    manifest_path: Path,
    models_dir: Path,
    *,
    subckt_dir: Path,
    global_models_dir: Path | None = None,
) -> MappingManifest:
    project = KiCadProject.load(design_dir)
    global_base = global_models_dir or subckt_dir.parent
    manifest = (
        load_manifest(manifest_path, global_models_dir=global_base)
        if manifest_path.exists()
        else MappingManifest()
    )
    manifest = sync_schematic_to_manifest(
        project,
        manifest,
        subckt_dir=subckt_dir,
    )
    save_manifest(manifest, manifest_path, global_models_dir=global_base)
    return manifest
