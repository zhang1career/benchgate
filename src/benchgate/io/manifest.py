"""Load/save models/manifest.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from benchgate.paths import benchgate_home
from benchgate.schemas import ComponentMapping, MappingManifest, MeasuredParams, SpiceModelKind


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
    if m.measured:
        d["measured"] = {
            "component_ref": m.measured.component_ref,
            "mpn": m.measured.mpn,
            "captured_at": m.measured.captured_at,
            "session_id": m.measured.session_id,
            "instrument": m.measured.instrument,
            "params": m.measured.params,
        }
    return d


def _mapping_from_dict(
    d: dict[str, Any],
    *,
    project_base: Path,
    global_models_base: Path,
) -> ComponentMapping:
    measured = None
    if "measured" in d:
        md = d["measured"]
        measured = MeasuredParams(
            component_ref=md["component_ref"],
            mpn=md["mpn"],
            captured_at=md["captured_at"],
            session_id=md["session_id"],
            instrument=md.get("instrument", {}),
            params=md.get("params", {}),
        )
    lib = d.get("sim_library")
    return ComponentMapping(
        kicad_key=d["kicad_key"],
        reference=d.get("reference"),
        spice_kind=SpiceModelKind(d.get("spice_kind", "unmapped")),
        sim_library=_resolve_sim_library(lib, global_models_base) if lib else None,
        sim_name=d.get("sim_name"),
        sim_pins=d.get("sim_pins"),
        measured=measured,
        metadata=d.get("metadata", {}),
    )


def load_manifest(path: Path, *, global_models_dir: Path | None = None) -> MappingManifest:
    project_base = path.parent
    global_base = global_models_dir or (benchgate_home() / "models")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    entries = [
        _mapping_from_dict(e, project_base=project_base, global_models_base=global_base)
        for e in data.get("entries", [])
    ]
    return MappingManifest(version=data.get("version", 1), entries=entries)


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
