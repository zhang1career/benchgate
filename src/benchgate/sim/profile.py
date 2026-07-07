"""Load and interpret ``sim_profiles.yaml`` blocks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_profile_block(config_path: Path, profile: str = "default") -> dict[str, Any]:
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    block = data.get(profile) or data.get("default") or {}
    return dict(block) if block else {}


def load_profile_checks(config_path: Path, profile: str = "default") -> list[dict]:
    checks = load_profile_block(config_path, profile).get("checks", [])
    return [dict(c) for c in checks] if checks else []


def load_profile_stress(config_path: Path, profile: str = "default") -> dict[str, Any]:
    stress = load_profile_block(config_path, profile).get("stress")
    return dict(stress) if stress else {}


def load_profile_fixup(config_path: Path, profile: str = "default"):
    from benchgate.sim.netlist_fixup import NetlistFixupConfig

    if not config_path.exists():
        return NetlistFixupConfig()
    block = load_profile_block(config_path, profile)
    return NetlistFixupConfig.from_profile(block)


def load_profile_save(config_path: Path, profile: str = "default") -> list[str]:
    save = load_profile_block(config_path, profile).get("save", [])
    return [str(s) for s in save] if save else []


def load_profile_operating_point(config_path: Path, profile: str = "default") -> dict[str, Any]:
    op = load_profile_block(config_path, profile).get("operating_point")
    return dict(op) if op else {}


def infer_operating_point(
    block: dict[str, Any],
    *,
    check_values: dict[str, float] | None = None,
) -> dict[str, float]:
    """Build operating_point from profile hints and completed check metrics."""
    op: dict[str, float] = {}
    infer = block.get("operating_point_infer") or {}
    checks_map = check_values or {}

    for dim, spec in infer.items():
        if isinstance(spec, dict):
            signal = spec.get("signal")
            metric = spec.get("metric", "avg")
            key = f"{signal}:{metric}" if signal else None
            if key and key in checks_map:
                op[dim] = checks_map[key]
            elif dim in checks_map:
                op[dim] = checks_map[dim]
        elif isinstance(spec, (int, float)):
            op[dim] = float(spec)

    # Supply from first V* DC directive when not already set.
    if "vsupply_v" not in op:
        for line in block.get("directives", []):
            text = str(line)
            if text.upper().startswith("V") and " DC " in text.upper():
                parts = text.upper().split(" DC ", 1)
                if len(parts) == 2:
                    try:
                        op["vsupply_v"] = float(parts[1].strip().split()[0])
                        break
                    except ValueError:
                        continue
    return op
