"""Picklable worker for parallel tolerance sample execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def execute_tolerance_sample(task: dict[str, Any]) -> dict[str, Any]:
    """Run one MC sample (optional coarse→fine); return point dict + metric values."""
    from benchgate.sim.analysis import analyze_raw_file
    from benchgate.sim.runner import run_ngspice
    from benchgate.sim.tolerance_sim import (
        TranPreset,
        apply_tran_preset,
        needs_fine_simulation,
    )
    from benchgate.sim.tolerance_sampling import apply_sample_to_netlist

    index = int(task["index"])
    base_text = str(task["base_text"])
    checks = list(task["checks"])
    output_dir = Path(task["output_dir"])
    design_dir = Path(task["design_dir"]) if task.get("design_dir") else None

    unit_row = task["unit_row"]
    import numpy as np

    text, overrides, u_norm, mix_choice, u_dim = apply_sample_to_netlist(
        base_text,
        component_axes=_deserialize_axes(task.get("component_axes", [])),
        env_axes=_deserialize_env_axes(task.get("env_axes", [])),
        mix_axes=_deserialize_mix_axes(task.get("mix_axes", [])),
        key_to_col={str(k): int(v) for k, v in task["key_to_col"].items()},
        unit_row=np.asarray(unit_row, dtype=float),
        design_dir=design_dir,
    )

    coarse_preset = _preset_from_dict(task.get("coarse_preset"))
    fine_preset = _preset_from_dict(task.get("fine_preset"))
    sim_tier = str(task.get("sim_tier", "auto"))
    refine_margin = float(task.get("refine_margin_pct", 5.0))

    def _run_tier(preset: TranPreset | None, tier_name: str) -> tuple[bool, dict[str, float], object | None]:
        netlist = apply_tran_preset(text, preset)
        point_cir = output_dir / f"pt{index:03d}_{tier_name}.cir"
        point_cir.write_text(netlist, encoding="utf-8")
        work = output_dir / f"pt{index:03d}_{tier_name}"
        work.mkdir(parents=True, exist_ok=True)
        result = run_ngspice(point_cir, work_dir=work)
        report = analyze_raw_file(result.raw_output, checks) if result.raw_output else None
        passed = bool(report and report.passed)
        metrics: dict[str, float] = {}
        if report:
            for chk, res in zip(checks, report.checks):
                key = _metric_key(chk)
                metrics[key] = res.value
        return passed, metrics, report

    used_tier = "fine"
    if sim_tier == "coarse" and coarse_preset:
        passed, metrics, _ = _run_tier(coarse_preset, "coarse")
        used_tier = "coarse"
    elif sim_tier == "fine" and fine_preset:
        passed, metrics, _ = _run_tier(fine_preset, "fine")
        used_tier = "fine"
    elif sim_tier == "auto" and coarse_preset and fine_preset:
        passed, metrics, _ = _run_tier(coarse_preset, "coarse")
        used_tier = "coarse"
        if needs_fine_simulation(
            metrics,
            checks,
            margin_pct=refine_margin,
            metric_key_fn=_metric_key,
        ):
            passed, metrics, _ = _run_tier(fine_preset, "fine")
            used_tier = "fine"
    else:
        passed, metrics, _ = _run_tier(fine_preset or coarse_preset, "run")
        used_tier = "fine" if fine_preset else "coarse" if coarse_preset else "profile"

    point = {
        "sample": index,
        "overrides": overrides,
        "passed": passed,
        "metrics": metrics,
        "u_norm": u_norm,
        "u_dim": u_dim,
        "mix_choice": mix_choice,
        "sim_tier": used_tier,
    }
    return {"index": index, "point": point, "metrics": metrics}


def _metric_key(check: dict, _value: float = float("nan")) -> str:
    alias = check.get("alias")
    if alias:
        return str(alias)
    return f"{check.get('signal')}:{check.get('metric')}"


def _preset_from_dict(raw: dict | None):
    from benchgate.sim.tolerance_sim import TranPreset

    if not raw:
        return None
    return TranPreset(
        id=str(raw.get("id", "preset")),
        tran_step=raw.get("tran_step"),
        tran_stop=raw.get("tran_stop"),
        maxstep=raw.get("maxstep"),
    )


def _deserialize_axes(raw: list[dict]):
    from benchgate.sim.tolerance_core import ToleranceAxis

    return [
        ToleranceAxis(
            ref=str(a["ref"]),
            nominal=str(a["nominal"]),
            distribution=str(a["distribution"]),
            tolerance_pct=float(a["tolerance_pct"]),
            group=a.get("group"),
            sample_key=str(a.get("sample_key", "")),
        )
        for a in raw
    ]


def _deserialize_env_axes(raw: list[dict]):
    from benchgate.sim.tolerance_sampling import EnvironmentAxis

    return [
        EnvironmentAxis(
            id=str(a["id"]),
            apply=str(a["apply"]),
            param=str(a.get("param", "")),
            nominal=float(a["nominal"]),
            distribution=str(a["distribution"]),
            tolerance_pct=a.get("tolerance_pct"),
            low=a.get("low"),
            high=a.get("high"),
            sample_key=str(a.get("sample_key", "")),
        )
        for a in raw
    ]


def _deserialize_mix_axes(raw: list[dict]):
    from benchgate.sim.tolerance_sampling import MixAxis, MixOption

    out = []
    for a in raw:
        options = [
            MixOption(
                id=str(o["id"]),
                weight=float(o["weight"]),
                tolerance_pct=o.get("tolerance_pct"),
                sim_name=o.get("sim_name"),
                sim_library=o.get("sim_library"),
            )
            for o in a.get("options", [])
        ]
        out.append(
            MixAxis(
                ref=str(a["ref"]),
                nominal=str(a["nominal"]),
                distribution=str(a["distribution"]),
                options=options,
                mix_kind=str(a.get("mix_kind", "value")),
                sample_key=str(a.get("sample_key", "")),
                value_key=str(a.get("value_key", "")),
            )
        )
    return out
