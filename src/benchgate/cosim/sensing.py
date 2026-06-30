"""Read output current from ngspice raw signals for cosim."""

from __future__ import annotations

import numpy as np


def _interp(time: np.ndarray, series: np.ndarray, t_query: float) -> float:
    if time.size == 0:
        return float("nan")
    return float(np.interp(t_query, time, series))


def read_iout_a(
    time: np.ndarray,
    signals: dict[str, np.ndarray],
    t_query: float,
    *,
    shunt_ohm: float,
    gain_vv: float,
    vout_v: float | None = None,
    rload_ohm: float | None = None,
    prefer_estimate: bool = False,
) -> tuple[float, str]:
    """
    Return (iout_a, source_tag).

    Default order: ADC_IOUT -> R51 branch -> Vout/Rload estimate.
    """
    isense_raw = signals.get("v(/h-bridge_power/isense_raw)")

    if prefer_estimate and vout_v is not None and rload_ohm and rload_ohm > 0:
        if isense_raw is not None:
            isense_v = _interp(time, isense_raw, t_query)
            return max(0.0, (vout_v - isense_v) / rload_ohm), "vout_diff"
        return max(0.0, vout_v / rload_ohm), "estimate"

    adc = signals.get("v(/sense_&_control/adc_iout)")
    if adc is not None:
        v_adc = _interp(time, adc, t_query)
        if np.isfinite(v_adc) and abs(v_adc) > 1e-9:
            denom = shunt_ohm * gain_vv
            if denom > 0:
                return v_adc / denom, "adc_iout"

    for key in ("vamm#branch", "r51#branch", "vr51#branch", "i(vamm)", "i(vr51)"):
        branch = signals.get(key)
        if branch is not None:
            i = _interp(time, branch, t_query)
            if np.isfinite(i):
                return abs(i), key.split("#")[0]

    if vout_v is not None and isense_raw is not None and rload_ohm and rload_ohm > 0:
        isense_v = _interp(time, isense_raw, t_query)
        return max(0.0, (vout_v - isense_v) / rload_ohm), "vout_diff"

    if vout_v is not None and rload_ohm and rload_ohm > 0:
        return max(0.0, vout_v / rload_ohm), "estimate"
    return 0.0, "none"
