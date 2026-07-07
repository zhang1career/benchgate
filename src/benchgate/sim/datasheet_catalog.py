"""Load default SPICE ``.model`` definitions from datasheet catalog YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from benchgate.paths import benchgate_home
from benchgate.sim.limits_catalog import match_catalog_part


def default_datasheet_catalog_path(home: Path | None = None) -> Path:
    root = benchgate_home(home)
    return root / "config" / "datasheet_models.yaml"


def load_datasheet_catalog(path: Path | None = None) -> dict[str, dict[str, Any]]:
    catalog_path = path or default_datasheet_catalog_path()
    if not catalog_path.exists():
        bundled = Path(__file__).resolve().parents[3] / "docs" / "examples" / "datasheet_models.yaml"
        if bundled.exists():
            catalog_path = bundled
        else:
            return {}

    data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    out: dict[str, dict[str, Any]] = {}
    for mpn, spec in data.items():
        if isinstance(spec, dict):
            out[str(mpn).upper()] = dict(spec)
    return out


def lookup_datasheet_model(mpn: str, catalog: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    key = match_catalog_part(mpn, {k: {} for k in catalog})
    return dict(catalog[key]) if key else None


def model_line(mpn: str, spec: dict[str, Any]) -> str:
    element = str(spec.get("element", "D")).upper()
    params = str(spec.get("params", "")).strip()
    name = mpn.upper()
    if not params:
        raise ValueError(f"datasheet model {name!r} has no params")
    return f".model {name} {element} ({params})"
