"""Scan KiCad schematic → manifest.yaml."""

from __future__ import annotations

import re
from pathlib import Path

from benchgate.io.manifest import load_manifest, save_manifest
from benchgate.kicad.project import KiCadProject, iter_symbols, symbol_key
from benchgate.kicad.spice_fields import read_sim_fields
from benchgate.schemas import ComponentMapping, MappingManifest, ModelProvenance, SpiceModelKind

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


def build_model(
    manifest: MappingManifest,
    entry: ComponentMapping,
    provider,
    *,
    workdir: Path,
) -> ComponentMapping:
    """Unified RFC provider entry: ``provider.build()`` → ``register_model()``."""
    from benchgate.providers.base import register_model

    artifact = provider.build(entry, workdir=workdir)
    return register_model(manifest, entry, artifact)


def apply_measured_model(
    manifest: MappingManifest,
    kicad_key: str,
    *,
    lib_path: Path,
    sim_name: str,
    sim_pins: str = "",
    measured_params: dict[str, float] | None = None,
    provenance: ModelProvenance | None = None,
) -> ComponentMapping:
    from benchgate.providers.bench import BenchModelProvider

    entry = manifest.find(kicad_key) or ComponentMapping(kicad_key=kicad_key)
    provider = BenchModelProvider(
        lib_path=lib_path,
        sim_name=sim_name,
        sim_pins=sim_pins or None,
        metrics=dict(measured_params or {}),
    )
    build_model(manifest, entry, provider, workdir=lib_path.parent)
    if provenance:
        entry.provenance = provenance
        if measured_params and entry.provenance.measured:
            entry.provenance.measured.params.update(measured_params)
        manifest.upsert(entry)
    return entry


def ensure_datasheet_models(
    manifest: MappingManifest,
    subckt_dir: Path,
    *,
    catalog_path: Path | None = None,
) -> int:
    """Build pending manifest entries that have a cataloged datasheet SPICE model."""
    from benchgate.providers.datasheet import DatasheetModelProvider
    from benchgate.sim.datasheet_catalog import load_datasheet_catalog, lookup_datasheet_model
    from benchgate.sim.limits_catalog import enrich_manifest_limits, match_catalog_part

    catalog = load_datasheet_catalog(catalog_path)
    if not catalog:
        return 0

    built = 0
    for entry in manifest.entries:
        if entry.is_ready:
            continue
        value = str((entry.metadata or {}).get("value") or "")
        mpn = match_catalog_part(value, catalog)
        if not mpn or not lookup_datasheet_model(mpn, catalog):
            continue
        provider = DatasheetModelProvider(mpn=mpn, catalog_path=catalog_path)
        build_model(manifest, entry, provider, workdir=subckt_dir)
        built += 1
    if built:
        enrich_manifest_limits(manifest)
    return built


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
    from benchgate.sim.limits_catalog import enrich_manifest_limits

    enrich_manifest_limits(manifest)
    ensure_datasheet_models(manifest, subckt_dir)
    save_manifest(manifest, manifest_path, global_models_dir=global_base)
    return manifest
