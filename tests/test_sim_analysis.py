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


def _write_ac_raw(path: Path, freq: np.ndarray, named: dict[str, np.ndarray]) -> None:
    """Write an AC-analysis raw file: Flags: complex, two doubles per value."""
    lines = ["\t0\tfrequency\tfrequency\n"]
    for i, name in enumerate(named, start=1):
        lines.append(f"\t{i}\t{name}\tvoltage\n")
    header = (
        "Title: test\n"
        "Plotname: AC Analysis\n"
        "Flags: complex\n"
        f"No. Variables: {len(named) + 1}\n"
        f"No. Points: {len(freq)}\n"
        "Variables:\n" + "".join(lines) + "Binary:\n"
    )
    columns = [freq.astype("<c16")] + [v.astype("<c16") for v in named.values()]
    matrix = np.column_stack(columns).astype("<c16")
    path.write_bytes(header.encode("latin-1") + matrix.tobytes())


def test_parse_ac_raw_returns_frequency_and_complex_signals(tmp_path: Path) -> None:
    """A complex raw file must not be read as real doubles.

    Doing so does not fail, it interleaves real and imaginary parts into the
    neighbouring variable, so the header flag has to be honoured.
    """
    freq = np.logspace(1, 5, 41)
    lp = 1.0 / (1.0 + 1j * freq / 1000.0)
    raw = tmp_path / "ac.raw"
    _write_ac_raw(raw, freq, {"v(out)": lp, "v(in)": np.ones_like(lp)})

    axis, signals = parse_ngspice_raw(raw)
    assert np.allclose(axis, freq)
    assert np.iscomplexobj(signals["v(out)"])
    assert np.allclose(signals["v(out)"], lp)


def test_ac_bandwidth_and_peaking_metrics(tmp_path: Path) -> None:
    from benchgate.sim.analysis import _compute_metric

    freq = np.logspace(1, 6, 2001)
    # first-order low-pass, -3 dB at exactly 1 kHz, no peaking
    lp = 1.0 / (1.0 + 1j * freq / 1000.0)
    # second-order, Q = 3.162: peak 20*log10(Q/sqrt(1-1/(4Q^2))) = 10.110 dB
    q, fn = np.sqrt(10.0), 5000.0
    s = 1j * freq / fn
    peaked = 1.0 / (1.0 + s / q + s**2)

    raw = tmp_path / "ac.raw"
    _write_ac_raw(raw, freq, {"v(lp)": lp, "v(pk)": peaked, "v(flat)": np.ones_like(lp)})
    axis, sig = parse_ngspice_raw(raw)

    assert np.isclose(_compute_metric(sig["v(lp)"], "bw_3db", axis=axis), 1000.0, rtol=2e-3)
    assert np.isclose(_compute_metric(sig["v(lp)"], "peaking_db", axis=axis), 0.0, atol=1e-6)

    peak_db = 20.0 * np.log10(q / np.sqrt(1.0 - 1.0 / (4.0 * q**2)))
    assert np.isclose(_compute_metric(sig["v(pk)"], "peaking_db", axis=axis), peak_db, atol=0.01)
    f_3db = fn * np.sqrt(
        1.0 - 1.0 / (2.0 * q**2) + np.sqrt(1.0 / (4.0 * q**4) - 1.0 / q**2 + 2.0)
    )
    assert np.isclose(_compute_metric(sig["v(pk)"], "bw_3db", axis=axis), f_3db, rtol=2e-3)

    # a response that never falls 3 dB has no bandwidth inside the swept range
    assert _compute_metric(sig["v(flat)"], "bw_3db", axis=axis) == float("inf")

    # resistive (real) first point → 0°; a lag has negative phase
    assert np.isclose(_compute_metric(sig["v(flat)"], "phase_deg_first"), 0.0, atol=1e-6)
    assert _compute_metric(sig["v(lp)"], "phase_deg_first") < 0.0
    # and bw_3db without an axis cannot be computed rather than being guessed
    assert np.isnan(_compute_metric(sig["v(lp)"], "bw_3db"))


def test_ac_time_domain_metric_falls_back_to_magnitude(tmp_path: Path) -> None:
    from benchgate.sim.analysis import _compute_metric

    freq = np.array([10.0, 100.0, 1000.0])
    values = np.array([3.0 + 4.0j, 0.0 + 1.0j, -2.0 + 0.0j])
    raw = tmp_path / "ac.raw"
    _write_ac_raw(raw, freq, {"v(out)": values})
    _, sig = parse_ngspice_raw(raw)

    assert np.isclose(_compute_metric(sig["v(out)"], "max"), 5.0)
    assert np.isclose(_compute_metric(sig["v(out)"], "min"), 1.0)
    assert np.isclose(_compute_metric(sig["v(out)"], "final"), 2.0)


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
