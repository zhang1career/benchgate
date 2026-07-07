"""Parse firmware board_config.h for cosim / SPICE codegen."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BoardConfig:
    pwm_freq_hz: float
    ctrl_loop_hz: float
    softstart_ms: float
    vin_div_ratio: float
    vout_div_ratio: float
    isense_shunt_ohm: float
    isense_gain_vv: float
    vin_uvlo_v: float
    vout_ovp_v: float
    iout_ocp_a: float
    deadtime_ns: float

    @property
    def pwm_period_s(self) -> float:
        return 1.0 / self.pwm_freq_hz

    @property
    def ctrl_dt_s(self) -> float:
        return 1.0 / self.ctrl_loop_hz


_DEFINE_RE = re.compile(r"^#define\s+(\w+)\s+([\d.]+)[fU]?\s*(?://.*)?$")
_DEFINE_EXPR_RE = re.compile(
    r"^#define\s+(\w+)\s+\(\(([\d.]+)f?\s*\+\s*([\d.]+)f?\)\s*/\s*([\d.]+)f?\)"
)


def _read_float(text: str, name: str, default: float | None = None) -> float:
    for line in text.splitlines():
        stripped = line.strip()
        expr = _DEFINE_EXPR_RE.match(stripped)
        if expr and expr.group(1) == name:
            a, b, c = map(float, expr.groups()[1:])
            return (a + b) / c
        match = _DEFINE_RE.match(stripped)
        if match and match.group(1) == name:
            return float(match.group(2))
    if default is not None:
        return default
    raise KeyError(f"missing #define {name}")


def load_board_config(path: Path) -> BoardConfig:
    text = path.read_text(encoding="utf-8")
    return BoardConfig(
        pwm_freq_hz=_read_float(text, "PWM_FREQ_HZ_DEFAULT"),
        ctrl_loop_hz=_read_float(text, "CTRL_LOOP_HZ"),
        softstart_ms=_read_float(text, "SOFTSTART_MS"),
        vin_div_ratio=_read_float(text, "VIN_DIV_RATIO"),
        vout_div_ratio=_read_float(text, "VOUT_DIV_RATIO"),
        isense_shunt_ohm=_read_float(text, "ISENSE_SHUNT_OHM"),
        isense_gain_vv=_read_float(text, "ISENSE_GAIN_VV"),
        vin_uvlo_v=_read_float(text, "VIN_UVLO_V"),
        vout_ovp_v=_read_float(text, "VOUT_OVP_V"),
        iout_ocp_a=_read_float(text, "IOUT_OCP_A"),
        deadtime_ns=_read_float(text, "DEADTIME_NS_DEFAULT"),
    )


def divider_ratio_from_netlist(netlist_text: str, adc_net: str, vin_net: str, gnd: str = "GND") -> float | None:
    """Return (Rtop+Rbot)/Rbot for a resistor divider driving adc_net."""
    top: str | None = None
    bot: str | None = None
    for line in netlist_text.splitlines():
        parts = line.strip().split()
        if not parts or parts[0][0] not in "Rr":
            continue
        if len(parts) < 4:
            continue
        _, n1, n2, value = parts[0], parts[1], parts[2], parts[3]
        value_u = value.lower()
        if not value_u.endswith(("k", "m", "r", "ohm")) and not value_u[0].isdigit():
            continue
        if n1.lower() == adc_net.lower() and n2.upper() == gnd:
            bot = value
        elif n2.lower() == adc_net.lower() and n1.upper() == gnd:
            bot = value
        elif n1.lower() == adc_net.lower() and n2.upper() == vin_net.upper():
            top = value
        elif n2.lower() == adc_net.lower() and n1.upper() == vin_net.upper():
            top = value
    if not top or not bot:
        return None
    return (_parse_r(top) + _parse_r(bot)) / _parse_r(bot)


def _parse_r(text: str) -> float:
    text = text.lower().strip()
    if text.endswith("k"):
        return float(text[:-1]) * 1e3
    if text.endswith("m"):
        return float(text[:-1]) * 1e-3
    if text.endswith("meg"):
        return float(text[:-3]) * 1e6
    return float(text.rstrip("r"))
