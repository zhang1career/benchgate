"""Load/save models/manifest.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from benchgate.paths import benchgate_home
from benchgate.schemas import (
    MANIFEST_VERSION,
    ComponentMapping,
    MappingManifest,
    MeasuredParams,
    ModelProvenance,
    ModelSource,
    SpiceModelKind,
)


def _rel(path: Path | None, base: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _resolve_sim_library(lib: str | Path, global_models_base: Path) -> Path:
    path = Path(lib)
    if path.is_absolute():
        return path
    return (global_models_base / path).resolve()


def _measured_to_dict(mp: MeasuredParams) -> dict[str, Any]:
    return {
        "component_ref": mp.component_ref,
        "mpn": mp.mpn,
        "captured_at": mp.captured_at,
        "session_id": mp.session_id,
        "instrument": mp.instrument,
        "params": mp.params,
    }


def _measured_from_dict(md: dict[str, Any]) -> MeasuredParams:
    return MeasuredParams(
        component_ref=md["component_ref"],
        mpn=md["mpn"],
        captured_at=md["captured_at"],
        session_id=md["session_id"],
        instrument=md.get("instrument", {}),
        params=md.get("params", {}),
    )


def _provenance_to_dict(pv: ModelProvenance) -> dict[str, Any]:
    d: dict[str, Any] = {"source": pv.source.value}
    if pv.generated_at:
        d["generated_at"] = pv.generated_at
    if pv.tool:
        d["tool"] = pv.tool
    if pv.source_files:
        d["source_files"] = pv.source_files
    if pv.checksum:
        d["checksum"] = pv.checksum
    if pv.valid_range:
        d["valid_range"] = pv.valid_range
    if pv.notes:
        d["notes"] = pv.notes
    if pv.measured:
        d["measured"] = _measured_to_dict(pv.measured)
    return d


def _provenance_from_dict(d: dict[str, Any]) -> ModelProvenance:
    return ModelProvenance(
        source=ModelSource(d["source"]),
        generated_at=d.get("generated_at", ""),
        tool=d.get("tool"),
        source_files=d.get("source_files", []),
        checksum=d.get("checksum"),
        valid_range=d.get("valid_range", {}),
        notes=d.get("notes"),
        measured=_measured_from_dict(d["measured"]) if "measured" in d else None,
    )


def _mapping_to_dict(
    m: ComponentMapping,
    *,
    project_base: Path,
    global_models_base: Path,
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "kicad_key": m.kicad_key,
        "spice_kind": m.spice_kind.value,
        "status": m.status,
        "metadata": m.metadata,
    }
    if m.reference:
        d["reference"] = m.reference
    lib = _rel(m.sim_library, global_models_base)
    if lib:
        d["sim_library"] = lib
    if m.sim_name:
        d["sim_name"] = m.sim_name
    if m.sim_pins:
        d["sim_pins"] = m.sim_pins
    if m.provenance:
        d["provenance"] = _provenance_to_dict(m.provenance)
    return d


def _mapping_from_dict(
    d: dict[str, Any],
    *,
    project_base: Path,
    global_models_base: Path,
) -> ComponentMapping:
    if "measured" in d:
        raise ValueError(
            f"manifest entry {d.get('kicad_key')!r} uses the legacy top-level 'measured' "
            "field. This is a v1 manifest; regenerate via `benchgate mapping sync` / "
            "`benchgate model build` (no backward compatibility)."
        )
    provenance = _provenance_from_dict(d["provenance"]) if "provenance" in d else None
    lib = d.get("sim_library")
    return ComponentMapping(
        kicad_key=d["kicad_key"],
        reference=d.get("reference"),
        spice_kind=SpiceModelKind(d.get("spice_kind", "unmapped")),
        sim_library=_resolve_sim_library(lib, global_models_base) if lib else None,
        sim_name=d.get("sim_name"),
        sim_pins=d.get("sim_pins"),
        provenance=provenance,
        metadata=d.get("metadata", {}),
    )


def load_manifest(path: Path, *, global_models_dir: Path | None = None) -> MappingManifest:
    project_base = path.parent
    global_base = global_models_dir or (benchgate_home() / "models")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    version = data.get("version", 1)
    if version < MANIFEST_VERSION:
        raise ValueError(
            f"manifest {path} is version {version}; benchgate requires version "
            f"{MANIFEST_VERSION}. Regenerate via `benchgate mapping sync` "
            "(no backward compatibility)."
        )
    entries = [
        _mapping_from_dict(e, project_base=project_base, global_models_base=global_base)
        for e in data.get("entries", [])
    ]
    return MappingManifest(version=version, entries=entries)


def save_manifest(
    manifest: MappingManifest,
    path: Path,
    *,
    global_models_dir: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    project_base = path.parent
    global_base = global_models_dir or (benchgate_home() / "models")
    data = {
        "version": manifest.version,
        "entries": [
            _mapping_to_dict(e, project_base=project_base, global_models_base=global_base)
            for e in manifest.entries
        ],
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
