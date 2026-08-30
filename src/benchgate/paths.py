"""Benchgate path resolution: global home vs design-local project paths."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


def benchgate_home(explicit: Path | str | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("BENCHGATE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".benchgate").resolve()


def resolve_design(design_dir: Path | str) -> Path:
    path = Path(design_dir).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def design_state_path(home: Path, design: Path) -> Path:
    digest = hashlib.sha256(str(design).encode()).hexdigest()[:16]
    return home / "state" / f"{digest}.json"


def benchgate_tmp_root() -> Path:
    return Path("/var/tmp/benchgate")


@dataclass(frozen=True)
class BenchgatePaths:
    home: Path
    design: Path
    models: Path
    manifest: Path
    lab_config: Path
    captured: Path
    global_models: Path
    subckt: Path
    config: Path
    sim_profile: Path
    instruments: Path
    reports: Path
    state: Path
    cosim_build: Path
    tmp_root: Path
    blocks_yaml: Path
    blocks_dir: Path


def resolve_project_path(design: Path, path: Path | str | None, default: Path) -> Path:
    if path is None:
        return default.resolve()
    resolved = Path(path).expanduser()
    if resolved.is_absolute():
        return resolved.resolve()
    return (design / resolved).resolve()


def benchgate_paths(
    design_dir: Path | str,
    *,
    manifest: Path | str | None = None,
    reports: Path | str | None = None,
    home: Path | str | None = None,
) -> BenchgatePaths:
    """Resolve paths. Project assets anchor on design; shared assets on BENCHGATE_HOME."""
    resolved_home = benchgate_home(home)
    design = resolve_design(design_dir)
    models = design / "models"

    manifest_path = Path(manifest).expanduser() if manifest else models / "manifest.yaml"
    if not manifest_path.is_absolute():
        manifest_path = design / manifest_path
    manifest_path = manifest_path.resolve()

    reports_path = Path(reports).expanduser() if reports else design / "reports"
    if not reports_path.is_absolute():
        reports_path = design / reports_path
    reports_path = reports_path.resolve()

    global_models = resolved_home / "models"
    global_sim_profile = resolved_home / "config" / "sim_profiles.yaml"
    local_sim_profile = models / "sim_profiles.yaml"
    return BenchgatePaths(
        home=resolved_home,
        design=design,
        models=models,
        manifest=manifest_path,
        lab_config=models / "lab.yaml",
        captured=models / "captured",
        global_models=global_models,
        subckt=global_models / "subckt",
        config=resolved_home / "config",
        sim_profile=local_sim_profile if local_sim_profile.is_file() else global_sim_profile,
        instruments=resolved_home / "config" / "instruments.yaml",
        reports=reports_path,
        state=design_state_path(resolved_home, design),
        cosim_build=resolved_home / "cosim",
        tmp_root=benchgate_tmp_root(),
        blocks_yaml=(models / "blocks.yaml").resolve(),
        blocks_dir=(models / "blocks").resolve(),
    )
