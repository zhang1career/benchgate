"""Tests for cosim current sensing helpers."""

from __future__ import annotations

import numpy as np

from benchgate.cosim.sensing import read_iout_a


def test_read_iout_from_adc() -> None:
    time = np.linspace(0, 1e-3, 10)
    adc = np.full(10, 0.26)
    signals = {"v(/sense_&_control/adc_iout)": adc}
    iout, src = read_iout_a(time, signals, 0.5e-3, shunt_ohm=0.01, gain_vv=100.0)
    assert src == "adc_iout"
    assert np.isclose(iout, 0.26)


def test_read_iout_from_differential_load() -> None:
    time = np.linspace(0, 1e-3, 10)
    signals = {
        "v(/sense_&_control/adc_iout)": np.full(10, 0.17),
        "v(/h-bridge_power/isense_raw)": np.full(10, 0.002),
    }
    iout, src = read_iout_a(
        time,
        signals,
        0.5e-3,
        shunt_ohm=0.01,
        gain_vv=100.0,
        vout_v=1.72,
        rload_ohm=10.0,
    )
    assert src == "adc_iout"
    assert np.isclose(iout, 0.17)

    signals.pop("v(/sense_&_control/adc_iout)")
    iout, src = read_iout_a(
        time,
        signals,
        0.5e-3,
        shunt_ohm=0.01,
        gain_vv=100.0,
        vout_v=1.72,
        rload_ohm=10.0,
    )
    assert src == "vout_diff"
    assert np.isclose(iout, 0.1718)
