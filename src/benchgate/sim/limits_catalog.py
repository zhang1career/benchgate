"""Load default component stress limits from catalog YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from benchgate.paths import benchgate_home


def default_catalog_path(home: Path | None = None) -> Path:
    root = benchgate_home(home)
    return root / "config" / "stress_limits.yaml"


def load_limits_catalog(path: Path | None = None) -> dict[str, dict[str, float]]:
    """Return {MPN_UPPER: {limit_key: value}}."""
    catalog_path = path or default_catalog_path()
    if not catalog_path.exists():
        bundled = Path(__file__).resolve().parents[3] / "docs" / "examples" / "stress_limits.yaml"
        if bundled.exists():
            catalog_path = bundled
        else:
            return {}

    data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    out: dict[str, dict[str, float]] = {}
    for mpn, limits in data.items():
        if not isinstance(limits, dict):
            continue
        out[str(mpn).upper()] = {str(k): float(v) for k, v in limits.items() if v is not None}
    return out


def lookup_part_limits(part: str, catalog: dict[str, dict[str, float]]) -> dict[str, float]:
    key = part.strip().upper()
    return dict(catalog.get(key, {}))


def merge_stress_limits(
    component_cfg: dict[str, Any],
    catalog: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Profile limits override catalog limits for the same keys."""
    part = str(component_cfg.get("part") or component_cfg.get("mpn") or "")
    merged = lookup_part_limits(part, catalog) if part else {}
    explicit = component_cfg.get("limits") or {}
    merged.update({str(k): float(v) for k, v in explicit.items()})
    return merged


def match_catalog_part(value: str, catalog: dict[str, dict[str, float]]) -> str | None:
    """Best-effort MPN match for a KiCad value string."""
    val = value.strip().upper()
    if not val:
        return None
    if val in catalog:
        return val
    for mpn in sorted(catalog.keys(), key=len, reverse=True):
        if mpn in val or val.startswith(mpn):
            return mpn
    return None


def enrich_manifest_limits(
    manifest,
    *,
    catalog_path: Path | None = None,
) -> int:
    """Attach datasheet stress limits from catalog into manifest ``spec.limits``."""
    from benchgate.schemas import MappingManifest

    if not isinstance(manifest, MappingManifest):
        return 0
    catalog = load_limits_catalog(catalog_path)
    if not catalog:
        return 0

    updated = 0
    for entry in manifest.entries:
        value = str((entry.metadata or {}).get("value") or "")
        mpn = match_catalog_part(value, catalog)
        if not mpn:
            continue
        limits = catalog[mpn]
        entry.spec = dict(entry.spec or {})
        if entry.spec.get("limits") != limits:
            entry.spec["limits"] = limits
            entry.spec["limits_mpn"] = mpn
            updated += 1
    return updated
