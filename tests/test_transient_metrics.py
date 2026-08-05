"""Tests for transient metric primitives."""

from __future__ import annotations

import numpy as np

from benchgate.sim.analysis import _compute_metric, evaluate_checks


def test_settling_time_01pct_step():
    t = np.linspace(0, 1e-6, 1000)
    y = np.ones_like(t)
    y[:200] = np.linspace(0, 1, 200)
    y[200:260] = 1.05
    y[260:] = 1.0
    val = _compute_metric(y, "settling_time_01pct", axis=t)
    assert 260 / 999 * 1e-6 * 0.8 < val < 400e-9


def test_overshoot_pct():
    t = np.linspace(0, 1e-6, 500)
    y = np.ones_like(t)
    y[:100] = np.linspace(0, 1, 100)
    y[100:150] = 1.2
    y[150:] = 1.0
    pct = _compute_metric(y, "overshoot_pct", axis=t)
    assert np.isclose(pct, 20.0, rtol=0.05)


def test_slew_rate_and_integral():
    t = np.linspace(0, 1e-6, 100)
    y = t * 1e6  # 1 V/us ramp
    slew = _compute_metric(y, "slew_rate", axis=t)
    assert np.isclose(slew, 1e6, rtol=0.05)
    charge = _compute_metric(np.full_like(t, 1e-3), "charge_nc", axis=t)
    assert np.isclose(charge, 1.0, rtol=0.05)


def test_evaluate_checks_settling_alias():
    t = np.linspace(0, 2e-6, 2000)
    y = np.ones_like(t)
    y[:400] = np.linspace(0, 2, 400)
    y[400:500] = 2.05
    y[500:] = 2.0
    report = evaluate_checks(
        t,
        {"v(out)": y},
        [{"signal": "v(out)", "metric": "settling_time_001pct", "alias": "settle_ns", "window_after": "0"}],
    )
    assert report.checks[0].alias == "settle_ns"
    assert report.checks[0].value > 0
