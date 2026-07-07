"""Load YAML rule packs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class RuleDef:
    id: str
    when: dict[str, Any]
    limit: dict[str, Any]
    severity: str
    evidence: str


@dataclass
class RulePack:
    id: str
    version: int
    source: str
    applies_to: list[str]
    severity_default: str
    path: Path
    rules: list[RuleDef] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def _bundled_rules_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "docs" / "examples" / "rules"


def default_rule_pack_paths(*, home: Path, design: Path) -> list[Path]:
    paths: list[Path] = []
    corp = home / "config" / "rules" / "corp-derating.yaml"
    if corp.is_file():
        paths.append(corp)
    else:
        bundled = _bundled_rules_dir() / "corp-derating.yaml"
        if bundled.is_file():
            paths.append(bundled)
    proj_dir = design / "models" / "rules"
    if proj_dir.is_dir():
        paths.extend(sorted(proj_dir.glob("*.yaml")))
    elif (design / "models" / "blocks.yaml").is_file():
        bundled_proj = _bundled_rules_dir() / "project-spec.yaml"
        if bundled_proj.is_file():
            paths.append(bundled_proj)
    return paths


def load_rule_pack(path: Path) -> RulePack:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules: list[RuleDef] = []
    for raw in data.get("rules") or []:
        rules.append(
            RuleDef(
                id=str(raw["id"]),
                when=dict(raw.get("when") or {}),
                limit=dict(raw.get("limit") or {}),
                severity=str(raw.get("severity") or data.get("severity_default") or "fail"),
                evidence=str(raw.get("evidence") or ""),
            )
        )
    return RulePack(
        id=str(data.get("id") or path.stem),
        version=int(data.get("version") or 1),
        source=str(data.get("source") or ""),
        applies_to=list(data.get("applies_to") or ["gate"]),
        severity_default=str(data.get("severity_default") or "fail"),
        path=path.resolve(),
        rules=rules,
        meta={k: v for k, v in data.items() if k not in {"id", "version", "source", "applies_to", "severity_default", "rules"}},
    )


def load_rule_packs(paths: list[Path]) -> list[RulePack]:
    packs: list[RulePack] = []
    for path in paths:
        if path.is_file():
            packs.append(load_rule_pack(path))
    return packs
