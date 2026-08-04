"""Tests for lab.waveform_metrics."""

from __future__ import annotations

import numpy as np
import pytest

from benchgate.lab import waveform_metrics as wm


def _square(low: float, high: float, n: int = 1000) -> np.ndarray:
    half = n // 2
    return np.concatenate([np.full(half, low), np.full(n - half, high)])


def test_steady_levels_ignores_spikes():
    v = _square(0.0, 5.0)
    v[0] = 12.0
    v[-1] = -8.0
    s = wm.steady_levels(v)
    assert s.vpp == pytest.approx(5.0, abs=0.2)
    assert s.vtop == pytest.approx(5.0, abs=0.2)
    assert s.vbase == pytest.approx(0.0, abs=0.2)


def test_peak_metrics_from_steady():
    v = _square(0.0, 5.0)
    v[0] = 7.0
    v[500] = -1.5
    steady = wm.steady_levels(v)
    peak = wm.peak_metrics(steady)
    assert peak.overshoot_pos == pytest.approx(7.0 - steady.vtop, abs=0.3)
    assert peak.undershoot_neg == pytest.approx(steady.vbase - (-1.5), abs=0.3)
    assert peak.vpp_raw > steady.vpp


def test_analyze_functional_omits_peak_fields():
    out = wm.analyze_waveform(_square(0.0, 5.0), "functional")
    assert out["vpp"] == pytest.approx(5.0, abs=0.1)
    assert "overshoot_frac" not in out


def test_analyze_performance_includes_peak_fields():
    v = _square(0.0, 5.0)
    v[10] = 6.5
    out = wm.analyze_waveform(v, "performance")
    assert out["profile"] == "performance"
    assert out["overshoot_frac"] > 0
    assert out["vpp"] == pytest.approx(5.0, abs=0.2)


def test_performance_pass_limits():
    metrics = {"overshoot_frac": 0.2, "undershoot_frac": 0.05}
    ok, reasons = wm.performance_pass(
        metrics, max_overshoot_frac=0.15, max_undershoot_frac=0.1
    )
    assert not ok
    assert any("overshoot" in r for r in reasons)


def test_correlation_identical_square():
    v = _square(0.0, 5.0)
    assert wm.correlation(v, v) == pytest.approx(1.0, abs=1e-6)
