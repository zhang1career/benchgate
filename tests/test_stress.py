"""Tests for component stress checks."""

from __future__ import annotations

import numpy as np

from benchgate.sim.stress import evaluate_stress


def test_evaluate_stress_margins() -> None:
    time = np.linspace(0, 1e-3, 50)
    signals = {
        "v(+12v)": np.full(50, 12.0),
        "v(emit)": np.full(50, 3.0),
        "v(base)": np.full(50, 3.7),
    }
    block = {
        "derating": 0.8,
        "window_after": "0.5ms",
        "components": {
            "Q1": {
                "limits": {"vceo": 25, "vebo": 5},
                "probes": {
                    "vce": {
                        "expr": "v(+12v) - v(emit)",
                        "metric": "max",
                        "abs": True,
                        "limit_key": "vceo",
                    },
                    "veb": {
                        "expr": "v(base) - v(emit)",
                        "metric": "max",
                        "abs": True,
                        "limit_key": "vebo",
                    },
                },
            }
        },
    }
    report = evaluate_stress(time, signals, block)
    assert report is not None
    assert report.passed is True
    vce = next(r for r in report.results if r.quantity == "vce")
    assert np.isclose(vce.value, 9.0)
    assert np.isclose(vce.derated_limit, 20.0)
    assert vce.margin_pct > 50


def test_evaluate_stress_power_and_current() -> None:
    time = np.linspace(0, 1e-3, 20)
    signals = {
        "v(+12v)": np.full(20, 12.0),
        "v(emit)": np.full(20, 2.0),
        "@q1[c]": np.full(20, 0.01),
    }
    block = {
        "derating": 0.8,
        "components": {
            "Q1": {
                "part": "SS8050",
                "probes": {
                    "ic": {
                        "type": "current",
                        "signal": "@q1[c]",
                        "metric": "max",
                        "abs": True,
                        "limit_key": "ic_max",
                    },
                    "pd": {
                        "type": "power",
                        "vce_expr": "v(+12v) - v(emit)",
                        "i_signal": "@q1[c]",
                        "metric": "max",
                        "limit_key": "pd_max",
                    },
                },
            }
        },
    }
    report = evaluate_stress(time, signals, block)
    assert report is not None
    assert report.passed is True
    pd = next(r for r in report.results if r.quantity == "pd")
    assert np.isclose(pd.value, 0.1)  # 10V * 0.01A
