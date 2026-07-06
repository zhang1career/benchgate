"""Continuous watch: re-run watch_once when design files change."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from benchgate.watch.trigger import watch_once


def watch_loop(
    design_dir: Path,
    *,
    manifest_path: Path,
    models_dir: Path,
    reports_dir: Path,
    state_path: Path,
    sim_profile_path: Path | None = None,
    profile: str = "default",
    subckt_dir: Path,
    global_models_dir: Path,
    blocks_yaml: Path | None = None,
    tmp_dir: Path | None = None,
    run_pipeline: bool = True,
    run_sim: bool = True,
    run_gate: bool = True,
    interval_s: float = 2.0,
    debounce_s: float = 1.0,
    max_iterations: int | None = None,
) -> dict[str, Any]:
    """Poll for KiCad/blocks changes and run the full agent pipeline on each batch."""
    design_dir = design_dir.resolve()
    iterations = 0
    last_result: dict[str, Any] = {
        "ran_at": None,
        "changed_files": [],
        "iterations": 0,
        "runs": [],
    }

    while max_iterations is None or iterations < max_iterations:
        result = watch_once(
            design_dir,
            manifest_path=manifest_path,
            models_dir=models_dir,
            reports_dir=reports_dir,
            state_path=state_path,
            sim_profile_path=sim_profile_path,
            profile=profile,
            subckt_dir=subckt_dir,
            global_models_dir=global_models_dir,
            blocks_yaml=blocks_yaml,
            tmp_dir=tmp_dir,
            run_pipeline=run_pipeline,
            run_sim=run_sim,
            run_gate=run_gate,
        )
        iterations += 1
        last_result["iterations"] = iterations
        last_result["last"] = result

        if result.get("changed_files"):
            last_result.setdefault("runs", []).append(result)

        if result.get("changed_files") and debounce_s > 0:
            time.sleep(debounce_s)

        if max_iterations is not None and iterations >= max_iterations:
            break
        time.sleep(max(interval_s, 0.1))

    return last_result
