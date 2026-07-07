"""Serialize sampling axes for parallel tolerance workers."""

from __future__ import annotations

from typing import Any

from benchgate.sim.tolerance_core import ToleranceAxis
from benchgate.sim.tolerance_sampling import EnvironmentAxis, MixAxis


def component_axes_to_dict(axes: list[ToleranceAxis]) -> list[dict[str, Any]]:
    return [
        {
            "ref": a.ref,
            "nominal": a.nominal,
            "distribution": a.distribution,
            "tolerance_pct": a.tolerance_pct,
            "group": a.group,
            "sample_key": a.sample_key,
        }
        for a in axes
    ]


def env_axes_to_dict(axes: list[EnvironmentAxis]) -> list[dict[str, Any]]:
    return [
        {
            "id": a.id,
            "apply": a.apply,
            "param": a.param,
            "nominal": a.nominal,
            "distribution": a.distribution,
            "tolerance_pct": a.tolerance_pct,
            "low": a.low,
            "high": a.high,
            "sample_key": a.sample_key,
        }
        for a in axes
    ]


def mix_axes_to_dict(axes: list[MixAxis]) -> list[dict[str, Any]]:
    return [
        {
            "ref": a.ref,
            "nominal": a.nominal,
            "distribution": a.distribution,
            "mix_kind": a.mix_kind,
            "sample_key": a.sample_key,
            "value_key": a.value_key,
            "options": [
                {
                    "id": o.id,
                    "weight": o.weight,
                    "tolerance_pct": o.tolerance_pct,
                    "sim_name": o.sim_name,
                    "sim_library": o.sim_library,
                }
                for o in a.options
            ],
        }
        for a in axes
    ]


def preset_to_dict(preset) -> dict[str, Any] | None:
    if preset is None:
        return None
    return {
        "id": preset.id,
        "tran_step": preset.tran_step,
        "tran_stop": preset.tran_stop,
        "maxstep": preset.maxstep,
    }
