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


def default_rule_pack_paths(*, home: Path, design: Path) -> list[Path]:
    """Rule packs to apply when the caller named none.

    Only packs the user actually owns count: the site-wide ones under
    ``$BENCHGATE_HOME/config/rules`` and the design's own ``models/rules``.

    The packs under ``docs/examples/rules`` are documentation, not defaults. They
    used to be loaded as a fallback, which made every project without its own
    pack inherit the charge-pump example's checks and fail sign-off on nets it
    does not have -- and, worse, a project *with* its own pack still inherited
    ``corp-derating-2024``, whose ``stress_derating_pass`` hard-fails unless the
    project happens to run stress sweeps.
    """
    paths: list[Path] = []
    for directory in (home / "config" / "rules", design / "models" / "rules"):
        if directory.is_dir():
            paths.extend(sorted(directory.glob("*.yaml")))
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
