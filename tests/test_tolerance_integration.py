"""Tests for watch tolerance hook, model mix, sequential stopping."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchgate.io.blocks_config import has_tolerance_study
from benchgate.sim.sequential import sequential_should_stop, wilson_yield_interval
from benchgate.sim.tolerance_sampling import (
    MixAxis,
    MixOption,
    _apply_temp,
    _swap_device_model,
    build_sampling_plan,
)


def test_has_tolerance_study(tmp_path: Path):
    blocks = tmp_path / "blocks.yaml"
    blocks.write_text("version: 1\n", encoding="utf-8")
    assert not has_tolerance_study(blocks)
    blocks.write_text("tolerances:\n  - ref: R1\n    tolerance_pct: 1\n", encoding="utf-8")
    assert has_tolerance_study(blocks)


def test_model_mix_plan_has_single_dimension():
    base = "Q1 VIN B E SS8050\n"
    tolerances = [
        {
            "ref": "Q1",
            "mix": [
                {"id": "a", "weight": 0.6, "sim_name": "SS8050", "sim_library": "a.lib"},
                {"id": "b", "weight": 0.4, "sim_name": "SS8050_LO", "sim_library": "b.lib"},
            ],
        }
    ]
    _, _, mix_axes, key_to_col, dims = build_sampling_plan(tolerances, [], base, {})
    assert len(mix_axes) == 1
    assert mix_axes[0].mix_kind == "model"
    assert len(key_to_col) == 1
    assert dims[0]["mix_kind"] == "model"


def test_swap_device_model_and_temp():
    text = ".title x\nQ1 A B C SS8050\n.control\n"
    out = _swap_device_model(text, "Q1", "SS8050_LO")
    assert "SS8050_LO" in out
    out2 = _apply_temp(out, 40.0)
    assert ".temp 40" in out2


def test_wilson_and_sequential_stop():
    y, lo, hi = wilson_yield_interval(95, 100)
    assert y == pytest.approx(95.0)
    assert lo < hi
    stop, info = sequential_should_stop(
        48, 50, min_samples=30, max_samples=200, ci_width_pct=5.0
    )
    assert info["ci_width_pct"] > 0
    stop2, info2 = sequential_should_stop(
        48, 50, min_samples=30, max_samples=50, ci_width_pct=100.0
    )
    assert stop2
    assert info2["stop_reason"] == "max_samples"


def test_mix_axis_model_only():
    axis = MixAxis(
        ref="Q1",
        nominal="SS8050",
        distribution="uniform",
        options=[
            MixOption(id="a", weight=1.0, sim_name="SS8050"),
        ],
        mix_kind="model",
        sample_key="mix:Q1",
        value_key="",
    )
    opt = axis.select_option(0.5)
    assert axis.sample_value(opt, 0.5) is None
