"""Unit tests for M3 surrogate and sampling plan helpers."""

from __future__ import annotations

import numpy as np
import pytest

from benchgate.sim.surrogate import fit_linear_surrogate, predict_yield_from_surrogates
from benchgate.sim.tolerance_sampling import (
    MixAxis,
    MixOption,
    build_sampling_plan,
    resolve_environment_nominal,
    warmup_sample_count,
)


def test_resolve_environment_nominal():
    raw = {"id": "vsupply", "nominal_from": "operating_point.vsupply_v"}
    assert resolve_environment_nominal(raw, {"vsupply_v": 12.0}) == 12.0


def test_build_sampling_plan_with_environment_and_mix():
    base = "R1 R 51k\nC2 C 22n\n"
    tolerances = [
        {
            "ref": "C2",
            "distribution": "uniform",
            "mix": [
                {"id": "a", "weight": 0.6, "tolerance_pct": 10},
                {"id": "b", "weight": 0.4, "tolerance_pct": 5},
            ],
        }
    ]
    environment = [
        {
            "id": "vsupply",
            "apply": "param",
            "param": "VSUP",
            "nominal": 12,
            "distribution": "uniform",
            "low": 10,
            "high": 15,
        }
    ]
    component_axes, env_axes, mix_axes, key_to_col, dims = build_sampling_plan(
        tolerances, environment, base, {"vsupply_v": 12}
    )
    assert len(component_axes) == 0
    assert len(mix_axes) == 1
    assert len(env_axes) == 1
    assert len(key_to_col) == 3
    kinds = {d["kind"] for d in dims}
    assert kinds == {"mix", "component_value", "environment"}


def test_mix_axis_select_option():
    axis = MixAxis(
        ref="C2",
        nominal="22n",
        distribution="uniform",
        options=[
            MixOption(id="a", weight=0.75, tolerance_pct=10),
            MixOption(id="b", weight=0.25, tolerance_pct=5),
        ],
        mix_kind="value",
        sample_key="mix:C2",
        value_key="ref:C2",
    )
    assert axis.select_option(0.1).id == "a"
    assert axis.select_option(0.9).id == "b"


def test_linear_surrogate_and_yield_probe():
    dim_keys = ["ref:R1", "env:vsupply"]
    rng = np.random.default_rng(0)
    points = []
    for _ in range(40):
        u1 = float(rng.random())
        u2 = float(rng.random())
        points.append(
            {
                "u_dim": {"ref:R1": u1, "env:vsupply": u2},
                "metrics": {"vout_avg": 20.0 + 2.0 * u1 - 1.0 * u2},
            }
        )
    model = fit_linear_surrogate(points, dim_keys=dim_keys, metric_key="vout_avg")
    assert model is not None
    assert model["r2"] == pytest.approx(1.0, abs=1e-4)
    checks = [{"alias": "vout_avg", "gte": 18, "lte": 24}]
    surrogate = {"vout_avg": model}
    yield_pct = predict_yield_from_surrogates(
        surrogate, checks=checks, dim_keys=dim_keys, n_probe=2000, seed=0
    )
    assert yield_pct == pytest.approx(100.0, abs=1.0)


def test_warmup_sample_count():
    assert warmup_sample_count(200, 3, 0.25) == 50
    assert warmup_sample_count(8, 3, 0.25) == 4
