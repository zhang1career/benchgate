"""Load ``models/blocks.yaml``: operating_point, circuit_spec, tolerances."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_blocks_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def circuit_spec_to_checks(circuit_spec: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Convert blocks ``circuit_spec.checks`` to sim profile check dicts."""
    if not circuit_spec:
        return []
    out: list[dict[str, Any]] = []
    for item in circuit_spec.get("checks") or []:
        bounds = item.get("bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            continue
        lo, hi = bounds[0], bounds[1]
        check: dict[str, Any] = {
            "signal": item["signal"],
            "metric": item.get("metric", "avg"),
        }
        if item.get("window_after") is not None:
            check["window_after"] = item["window_after"]
        if item.get("id"):
            check["alias"] = item["id"]
        if lo is not None:
            check["gte"] = float(lo)
        if hi is not None:
            check["lte"] = float(hi)
        out.append(check)
    return out


def load_tolerances(path: Path) -> list[dict[str, Any]]:
    data = load_blocks_yaml(path)
    tol = data.get("tolerances") or []
    return [dict(t) for t in tol] if isinstance(tol, list) else []


def load_environment(path: Path) -> list[dict[str, Any]]:
    data = load_blocks_yaml(path)
    env = data.get("environment") or []
    return [dict(e) for e in env] if isinstance(env, list) else []


def has_tolerance_study(path: Path) -> bool:
    """True when blocks.yaml defines tolerances and/or environment perturbation axes."""
    data = load_blocks_yaml(path)
    tol = data.get("tolerances") or []
    env = data.get("environment") or []
    return bool(tol) or bool(env)
