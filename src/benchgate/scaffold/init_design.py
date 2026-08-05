"""Scaffold a minimal benchgate layout under a KiCad design directory."""

from __future__ import annotations

from pathlib import Path

BLOCKS_YAML = """# benchgate blocks — edit spec and add models/blocks/*.net
version: 1

operating_point:
  vsupply_v: 5.0
  temp_c: 25

blocks: []
"""

PROJECT_SPEC_YAML = """# Local gate rules. Keep this file even if empty: it prevents benchgate
# from falling back to bundled example packs meant for other demos.
id: project-spec
version: 1
source: design models/blocks.yaml
applies_to:
  - gate
rules: []
"""

LAB_YAML = """# Optional bench roles — instrument addresses live in ~/.benchgate/config/instruments.yaml
version: 1

roles:
  scope: scope_main
  dmm: dmm_bench
"""


def init_design(design_dir: Path, *, force: bool = False) -> dict:
    design_dir = design_dir.resolve()
    if not design_dir.is_dir():
        raise FileNotFoundError(f"design directory not found: {design_dir}")

    models = design_dir / "models"
    blocks = models / "blocks"
    rules = models / "rules"
    captured = models / "captured" / "sessions"
    reports = design_dir / "reports" / "sim"

    for path in (blocks, rules, captured, reports):
        path.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []

    def _write(rel: Path, content: str) -> None:
        if rel.exists() and not force:
            skipped.append(str(rel.relative_to(design_dir)))
            return
        rel.write_text(content, encoding="utf-8")
        created.append(str(rel.relative_to(design_dir)))

    _write(models / "blocks.yaml", BLOCKS_YAML)
    _write(rules / "project-spec.yaml", PROJECT_SPEC_YAML)
    _write(models / "lab.yaml", LAB_YAML)

    return {
        "design_dir": str(design_dir),
        "created": created,
        "skipped": skipped,
    }
