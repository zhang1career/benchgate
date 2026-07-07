"""Stress evaluation degradation when probes are missing."""

from __future__ import annotations

import numpy as np

from benchgate.sim.stress import evaluate_stress


def test_stress_on_missing_warn_by_default() -> None:
    time = np.linspace(0, 1e-3, 10)
    signals = {"v(+12v)": np.full(10, 12.0)}
    block = {
        "derating": 0.8,
        "on_missing": "warn",
        "components": {
            "Q1": {
                "limits": {"vceo": 25},
                "probes": {
                    "vce": {
                        "expr": "v(+12v) - v(missing)",
                        "metric": "max",
                        "limit_key": "vceo",
                    },
                    "ic": {
                        "type": "current",
                        "signal": "@q1[ic]",
                        "metric": "max",
                        "limit_key": "ic_max",
                        "required": False,
                    },
                },
            }
        },
    }
    report = evaluate_stress(time, signals, block)
    assert report is not None
    assert report.passed is True
    assert report.warnings
    vce = next(r for r in report.results if r.quantity == "vce")
    assert vce.severity == "warn"
    assert vce.passed is True


def test_stress_on_missing_fail_when_configured() -> None:
    time = np.linspace(0, 1e-3, 10)
    block = {
        "derating": 0.8,
        "on_missing": "fail",
        "components": {
            "Q1": {
                "limits": {"vceo": 25},
                "probes": {
                    "vce": {
                        "expr": "v(+12v) - v(missing)",
                        "metric": "max",
                        "limit_key": "vceo",
                    },
                },
            }
        },
    }
    report = evaluate_stress(time, {}, block)
    assert report is not None
    assert report.passed is False
    vce = next(r for r in report.results if r.quantity == "vce")
    assert vce.severity == "fail"
