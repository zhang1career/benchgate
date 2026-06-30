"""Tests for the lab analysis layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from benchgate.instruments.types import Waveform
from benchgate.lab import analyze


def _wf(t, v):
    return Waveform(
        time_s=np.asarray(t, dtype=float),
        voltage_v=np.asarray(v, dtype=float),
        channel=1,
        timestamp=datetime.now(timezone.utc),
    )


def test_crop_window():
    wf = _wf(np.linspace(0, 1, 11), np.linspace(0, 10, 11))
    c = analyze.crop(wf, 0.2, 0.5)
    assert c.time_s.min() >= 0.2 - 1e-12
    assert c.time_s.max() <= 0.5 + 1e-12
    assert len(c) == 4  # 0.2,0.3,0.4,0.5


def test_resample_uniform_num():
    wf = _wf([0.0, 1.0, 2.0], [0.0, 10.0, 20.0])
    r = analyze.resample_uniform(wf, num=5)
    assert len(r) == 5
    # linear data -> interpolation preserves the line
    assert r.voltage_v[2] == pytest.approx(10.0)


def test_align_and_overlay():
    a = _wf(np.linspace(0, 1, 100), np.ones(100) * 2.0)
    b = _wf(np.linspace(0, 1, 80), np.ones(80) * 4.0)
    grid, matrix = analyze.align_waveforms([a, b])
    assert matrix.shape[0] == 2
    assert grid.size == matrix.shape[1]
    env = analyze.overlay([a, b])
    assert env.mean[0] == pytest.approx(3.0)
    assert env.vmin[0] == pytest.approx(2.0)
    assert env.vmax[0] == pytest.approx(4.0)


def test_align_requires_overlap():
    a = _wf([0.0, 1.0], [0.0, 1.0])
    b = _wf([2.0, 3.0], [0.0, 1.0])
    with pytest.raises(ValueError):
        analyze.align_waveforms([a, b])


def test_compare_waveforms_identical():
    t = np.linspace(0, 1, 50)
    v = np.exp(-t / 0.2)
    cmp = analyze.compare_waveforms(_wf(t, v), _wf(t, v))
    assert cmp.rmse == pytest.approx(0.0, abs=1e-9)
    assert cmp.max_abs_err == pytest.approx(0.0, abs=1e-9)
    assert cmp.correlation == pytest.approx(1.0, abs=1e-6)


def test_compare_waveforms_offset():
    t = np.linspace(0, 1, 50)
    a = _wf(t, np.linspace(0, 1, 50))
    b = _wf(t, np.linspace(0, 1, 50) + 0.5)
    cmp = analyze.compare_waveforms(a, b)
    assert cmp.rmse == pytest.approx(0.5, abs=1e-6)
    assert cmp.max_abs_err == pytest.approx(0.5, abs=1e-6)


def _rows(values, start=None, step_min=1):
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {"captured_at": start + timedelta(minutes=i * step_min), "session_id": f"s{i}", "value": v}
        for i, v in enumerate(values)
    ]


def test_metric_stats():
    s = analyze.metric_stats(_rows([1.0, 2.0, 3.0]))
    assert s.n == 3
    assert s.mean == pytest.approx(2.0)
    assert s.vmin == pytest.approx(1.0)
    assert s.vmax == pytest.approx(3.0)


def test_drift_positive_slope():
    # +1 unit per minute -> slope per second = 1/60
    d = analyze.drift(_rows([10.0, 11.0, 12.0, 13.0], step_min=1))
    assert d.n == 4
    assert d.slope_per_s == pytest.approx(1.0 / 60.0, rel=1e-6)
    assert d.first == 10.0
    assert d.last == 13.0
    assert d.span_s == pytest.approx(180.0)


def test_drift_single_point():
    d = analyze.drift(_rows([5.0]))
    assert d.n == 1
    assert d.slope_per_s == 0.0


def test_drift_empty():
    d = analyze.drift([])
    assert d.n == 0
    assert np.isnan(d.slope_per_s)


def test_compare_runs():
    c = analyze.compare_runs(2.0, 2.2)
    assert c.delta == pytest.approx(0.2)
    assert c.rel_error == pytest.approx(0.1)
