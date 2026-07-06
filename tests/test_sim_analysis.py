"""Tests for ngspice raw parsing and profile checks."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from benchgate.sim.analysis import evaluate_checks, parse_ngspice_raw


def _write_minimal_raw(path: Path, time: np.ndarray, vout: np.ndarray, vin: np.ndarray) -> None:
    header = (
        "Title: test\n"
        "Plotname: Transient Analysis\n"
        "Flags: real\n"
        "No. Variables: 3\n"
        f"No. Points: {len(time)}\n"
        "Variables:\n"
        "\t0\ttime\ttime\n"
        "\t1\tv(/h-bridge_power/vout)\tvoltage\n"
        "\t2\tv(vin_port)\tvoltage\n"
        "Binary:\n"
    )
    matrix = np.column_stack([time, vout, vin]).astype("<f8")
    path.write_bytes(header.encode("latin-1") + matrix.tobytes())


def test_parse_ngspice_raw(tmp_path: Path) -> None:
    time = np.linspace(0, 1e-3, 5)
    vout = np.linspace(0, 2, 5)
    vin = np.full(5, 12.0)
    raw = tmp_path / "sim.raw"
    _write_minimal_raw(raw, time, vout, vin)

    parsed_time, signals = parse_ngspice_raw(raw)
    assert parsed_time.shape == (5,)
    assert np.isclose(signals["v(/h-bridge_power/vout)"][-1], 2.0)
    assert np.isclose(signals["v(vin_port)"].mean(), 12.0)


def test_evaluate_checks_pass_and_fail(tmp_path: Path) -> None:
    time = np.linspace(0, 1e-3, 100)
    vout = np.concatenate([np.zeros(50), np.full(50, 1.2)])
    vin = np.full(100, 12.0)
    raw = tmp_path / "sim.raw"
    _write_minimal_raw(raw, time, vout, vin)
    _, signals = parse_ngspice_raw(raw)

    passing = evaluate_checks(
        time,
        signals,
        [
            {"signal": "v(/h-bridge_power/vout)", "window_after": "0.5ms", "metric": "min", "gte": 1.0},
            {"signal": "v(vin_port)", "metric": "avg", "gte": 11.5, "lte": 12.5},
        ],
    )
    assert passing.passed is True

    failing = evaluate_checks(
        time,
        signals,
        [{"signal": "v(/h-bridge_power/vout)", "window_after": "0.5ms", "metric": "min", "gte": 2.0}],
    )
    assert failing.passed is False
    assert failing.checks[0].passed is False

    ripple = evaluate_checks(
        time,
        signals,
        [{"signal": "v(/h-bridge_power/vout)", "metric": "pp", "gte": 0.5, "lte": 1.5}],
    )
    assert ripple.passed is True
    assert np.isclose(ripple.checks[0].value, 1.2)


def test_evaluate_expression_check(tmp_path: Path) -> None:
    time = np.linspace(0, 1e-3, 100)
    v_hi = np.full(100, 12.0)
    v_lo = np.concatenate([np.zeros(50), np.full(50, 3.0)])
    header = (
        "Title: test\nPlotname: Transient Analysis\nFlags: real\n"
        "No. Variables: 3\nNo. Points: 100\nVariables:\n"
        "\t0\ttime\ttime\n"
        "\t1\tv(+12v)\tvoltage\n"
        "\t2\tv(emit)\tvoltage\n"
        "Binary:\n"
    )
    matrix = np.column_stack([time, v_hi, v_lo]).astype("<f8")
    raw = tmp_path / "sim.raw"
    raw.write_bytes(header.encode("latin-1") + matrix.tobytes())
    _, signals = parse_ngspice_raw(raw)

    report = evaluate_checks(
        time,
        signals,
        [
            {
                "expr": "v(+12v) - v(emit)",
                "window_after": "0.5ms",
                "metric": "max",
                "gte": 8.0,
            }
        ],
    )
    assert report.passed is True
    assert np.isclose(report.checks[0].value, 9.0)


def test_resolve_branch_current_alias() -> None:
    from benchgate.sim.analysis import _resolve_signal

    signals = {"@q1[c]": np.array([0.01, 0.02, 0.03])}
    series = _resolve_signal(signals, "i(q1)")
    assert series is not None
    assert np.isclose(series[-1], 0.03)


def test_evaluate_adc_signal_names(tmp_path: Path) -> None:
    time = np.linspace(0, 5e-3, 100)
    adc_vin = np.full(100, 1.08)
    header = (
        "Title: test\n"
        "Plotname: Transient Analysis\n"
        "Flags: real\n"
        "No. Variables: 2\n"
        f"No. Points: {len(time)}\n"
        "Variables:\n"
        "\t0\ttime\ttime\n"
        "\t1\tv(/sense_&_control/adc_vin)\tvoltage\n"
        "Binary:\n"
    )
    matrix = np.column_stack([time, adc_vin]).astype("<f8")
    raw = tmp_path / "sim.raw"
    raw.write_bytes(header.encode("latin-1") + matrix.tobytes())
    parsed_time, signals = parse_ngspice_raw(raw)

    report = evaluate_checks(
        parsed_time,
        signals,
        [
            {
                "signal": "v(/sense_&_control/adc_vin)",
                "window_after": "4ms",
                "metric": "avg",
                "gte": 0.95,
                "lte": 1.15,
            }
        ],
    )
    assert report.passed is True
    assert np.isclose(report.checks[0].value, 1.08)
