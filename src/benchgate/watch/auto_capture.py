"""Auto-trigger lab capture for pending manifest entries during watch pipeline."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import yaml

from benchgate.schemas import ComponentMapping, MappingManifest, SpiceModelKind

_DEFAULT_SKIP_LIB_PREFIXES = (
    "Connector:",
    "power:",
    "Mechanical:",
)
_CONNECTOR_REF_RE = re.compile(r"^J\d+$", re.I)


def load_auto_capture_config(models_dir: Path) -> dict[str, Any]:
    path = models_dir / "auto_capture.yaml"
    if not path.is_file():
        return {"enabled": True, "max_per_run": 5, "require_bench": True}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {"enabled": True, "max_per_run": 5, "require_bench": True}
    return data


def _skip_prefixes(config: dict[str, Any]) -> tuple[str, ...]:
    extra = config.get("skip_lib_id_prefixes") or []
    return _DEFAULT_SKIP_LIB_PREFIXES + tuple(extra)


def is_auto_capture_candidate(entry: ComponentMapping, config: dict[str, Any]) -> bool:
    if entry.status != "pending":
        return False
    if entry.spice_kind in (SpiceModelKind.PASSIVE, SpiceModelKind.BUILTIN):
        return False
    ref = entry.reference or ""
    skip_refs = {str(r) for r in (config.get("skip_references") or [])}
    if ref in skip_refs or _CONNECTOR_REF_RE.match(ref):
        return False
    lib_id = str((entry.metadata or {}).get("lib_id") or "")
    for prefix in _skip_prefixes(config):
        if lib_id.startswith(prefix):
            return False
    return entry.spice_kind == SpiceModelKind.SUBCKT


def bench_configured(lab_config: Path, instruments_config: Path) -> bool:
    return lab_config.is_file() or instruments_config.is_file()


def run_auto_capture(
    design_dir: Path,
    manifest: MappingManifest,
    *,
    models_dir: Path,
    lab_config: Path,
    instruments_config: Path,
    dispatch_fn: Callable[[str, dict[str, Any]], Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Queue ``lab_capture`` for pending subckt entries that need bench models."""
    from benchgate.agent.dispatch import dispatch as default_dispatch

    dispatch = dispatch_fn or default_dispatch
    config = load_auto_capture_config(models_dir)
    if not config.get("enabled", True):
        return {"enabled": False, "captures": [], "candidates": 0}

    candidates = [e for e in manifest.entries if is_auto_capture_candidate(e, config)]
    max_per_run = int(config.get("max_per_run", 5))
    candidates = candidates[:max_per_run]

    require_bench = bool(config.get("require_bench", True))
    has_bench = bench_configured(lab_config, instruments_config)
    if require_bench and not has_bench and not dry_run:
        return {
            "enabled": True,
            "candidates": len(candidates),
            "captures": [
                {
                    "kicad_key": e.kicad_key,
                    "reference": e.reference,
                    "status": "skipped",
                    "reason": "no lab.yaml or instruments.yaml configured",
                }
                for e in candidates
            ],
        }

    captures: list[dict[str, Any]] = []
    for entry in candidates:
        ref = entry.reference or entry.kicad_key
        mpn = str((entry.metadata or {}).get("value") or ref)
        item: dict[str, Any] = {
            "kicad_key": entry.kicad_key,
            "reference": ref,
            "mpn": mpn,
        }
        if dry_run:
            item["status"] = "dry_run"
            captures.append(item)
            continue
        try:
            result = dispatch(
                "lab_capture",
                {
                    "design_dir": str(design_dir),
                    "component_ref": ref,
                    "mpn": mpn,
                    "kicad_key": entry.kicad_key,
                },
            )
            item["status"] = "ok"
            item["result"] = result
        except Exception as exc:  # noqa: BLE001 — collect per-entry errors for watch report
            item["status"] = "error"
            item["error"] = str(exc)
        captures.append(item)

    return {"enabled": True, "candidates": len(candidates), "captures": captures}
