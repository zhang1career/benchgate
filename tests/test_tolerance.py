"""Unit tests for tolerance sampling (no ngspice)."""

from __future__ import annotations

import numpy as np
import pytest

from benchgate.sim.sensitivity import compute_sensitivity, spearman_correlation, top_sensitivity_drivers
from benchgate.sim.tolerance import (
    ToleranceAxis,
    build_sampling_axes,
    format_spice_number,
    lhs_unit,
    parse_spice_number,
)


def test_parse_and_format_spice_number():
    assert parse_spice_number("51k") == 51000.0
    assert parse_spice_number("22n") == pytest.approx(2.2e-8)
    out = format_spice_number(51510.0, "51k")
    assert out.endswith("k")


def test_lhs_unit_coverage():
    u = lhs_unit(200, 3, np.random.default_rng(0))
    assert u.shape == (200, 3)
    assert np.all(u >= 0) and np.all(u <= 1)


def test_tolerance_axis_uniform():
    axis = ToleranceAxis(ref="R1", nominal="51k", distribution="uniform", tolerance_pct=1.0)
    lo = axis.sample_value(0.0)
    hi = axis.sample_value(1.0)
    assert parse_spice_number(lo) < parse_spice_number(hi)


def test_group_shares_lhs_dimension():
    base = "R1 R 51k\nR2 R 22k\nC2 C 22n\n"
    axes_raw = [
        {"ref": "R1", "group": "timing", "distribution": "uniform", "tolerance_pct": 1.0},
        {"ref": "R2", "group": "timing", "distribution": "uniform", "tolerance_pct": 1.0},
        {"ref": "C2", "distribution": "uniform", "tolerance_pct": 10.0},
    ]
    axes, key_to_col, sampling_dims = build_sampling_axes(axes_raw, base)
    assert len(key_to_col) == 2
    assert axes[0].sample_key == axes[1].sample_key
    assert axes[2].sample_key != axes[0].sample_key
    assert len(sampling_dims) == 2
    timing_dim = next(d for d in sampling_dims if d.get("group") == "timing")
    assert set(timing_dim["refs"]) == {"R1", "R2"}

    rng = np.random.default_rng(7)
    unit = lhs_unit(50, len(key_to_col), rng)
    u_by_ref: list[dict[str, float]] = []
    for i in range(50):
        row: dict[str, float] = {}
        for axis in axes:
            row[axis.ref] = float(unit[i, key_to_col[axis.sample_key]])
        u_by_ref.append(row)
    for row in u_by_ref:
        assert row["R1"] == row["R2"]


def test_spearman_and_sensitivity():
    x = np.linspace(0, 1, 20)
    y = x**2
    assert spearman_correlation(x, y) == pytest.approx(1.0, abs=1e-6)

    points = [
        {"u_norm": {"R1": float(u), "C2": 1.0 - float(u)}, "metrics": {"vout": float(u)}}
        for u in x
    ]
    sens = compute_sensitivity(points, axis_refs=["R1", "C2"], metric_keys=["vout"])
    assert sens["vout"]["R1"] == pytest.approx(1.0, abs=1e-6)
    assert sens["vout"]["C2"] == pytest.approx(-1.0, abs=1e-6)
    drivers = top_sensitivity_drivers(sens, limit=1)
    assert drivers["vout"][0]["ref"] == "R1"
