"""Continuous watch: re-run watch_once when design files change."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from benchgate.watch.trigger import watch_once

logger = logging.getLogger(__name__)


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
    run_auto_capture: bool = True,
    auto_capture_dry_run: bool = False,
    run_tolerance: bool = True,
    tolerance_samples: int = 200,
    tolerance_strategy: str = "auto",
    tolerance_seed: int = 42,
    tolerance_jobs: int = 4,
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
        try:
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
                run_auto_capture=run_auto_capture,
                auto_capture_dry_run=auto_capture_dry_run,
                run_tolerance=run_tolerance,
                tolerance_samples=tolerance_samples,
                tolerance_strategy=tolerance_strategy,
                tolerance_seed=tolerance_seed,
                tolerance_jobs=tolerance_jobs,
            )
        except Exception as exc:  # noqa: BLE001 — keep polling across transient export/sim failures
            logger.exception("watch_once failed; continuing poll loop")
            result = {
                "ran_at": None,
                "changed_files": [],
                "triggered_sessions": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        iterations += 1
        last_result["iterations"] = iterations
        last_result["last"] = result

        if result.get("changed_files") or result.get("triggered_sessions") or result.get("error"):
            last_result.setdefault("runs", []).append(result)
            if result.get("error"):
                print(f"[watch] error: {result['error']}", flush=True)
            elif not result.get("skipped"):
                sim = result.get("sim") or {}
                gate = (result.get("gate") or {}).get("summary") or {}
                print(
                    f"[watch] triggered files={len(result.get('changed_files') or [])} "
                    f"ngspice_ok={sim.get('ngspice_ok')} "
                    f"spec_failures={gate.get('spec_failures')}",
                    flush=True,
                )

        if result.get("changed_files") and debounce_s > 0:
            time.sleep(debounce_s)

        if max_iterations is not None and iterations >= max_iterations:
            break
        time.sleep(max(interval_s, 0.1))

    return last_result
