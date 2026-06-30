"""Generate PWM drive lines and mcu_pwm_ctrl.lib from board_config.h."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from benchgate.cosim.board_config import BoardConfig


class StageMode(IntEnum):
    AUTO = 0
    BUCK = 1
    BOOST = 2
    BUCKBOOST = 3


@dataclass(frozen=True)
class PwmDrive:
    hin1: str
    lin1: str
    hin2: str
    lin2: str


def resolve_stage(vin_v: float, vout_v: float, stage: StageMode) -> StageMode:
    if stage != StageMode.AUTO:
        return stage
    if vin_v > (vout_v + 0.5):
        return StageMode.BUCK
    if vin_v < (vout_v - 0.5):
        return StageMode.BOOST
    return StageMode.BUCKBOOST


def pulse_source(name: str, node: str, gnd: str, delay_s: float, pw_s: float, period_s: float, vhi: float = 3.3) -> str:
    tr = min(100e-9, pw_s / 10.0 if pw_s > 0 else 100e-9)
    if pw_s <= 0:
        return f"{name} {node} {gnd} DC 0"
    return (
        f"{name} {node} {gnd} PULSE(0 {vhi:g} {delay_s:g} {tr:g} {tr:g} {pw_s:g} {period_s:g})"
    )


def drive_four_switch_complementary(
    duty: float,
    cfg: BoardConfig,
    *,
    hin1: str = "/Gate_Drive/PWM_HIN1",
    lin1: str = "/Gate_Drive/PWM_LIN1",
    hin2: str = "/Gate_Drive/PWM_HIN2",
    lin2: str = "/Gate_Drive/PWM_LIN2",
    gnd: str = "GND",
    period_s: float | None = None,
) -> PwmDrive:
    """Match hbridge_pwm profile: phase-A HIN1+LIN2, phase-B LIN1+HIN2."""
    period = period_s if period_s is not None else cfg.pwm_period_s
    pw = max(0.0, min(duty, 0.95)) * period
    half = period / 2.0
    tr = min(100e-9, pw / 10.0 if pw > 0 else 100e-9)

    def pulse(name: str, node: str, delay: float) -> str:
        return pulse_source(name, node, gnd, delay, pw, period)

    return PwmDrive(
        hin1=pulse("VPWM_HIN1", hin1, 0),
        lin1=pulse("VPWM_LIN1", lin1, half),
        hin2=pulse("VPWM_HIN2", hin2, half),
        lin2=pulse("VPWM_LIN2", lin2, 0),
    )


def drive_for_stage(
    stage: StageMode,
    duty: float,
    cfg: BoardConfig,
    *,
    hin1: str = "/Gate_Drive/PWM_HIN1",
    lin1: str = "/Gate_Drive/PWM_LIN1",
    hin2: str = "/Gate_Drive/PWM_HIN2",
    lin2: str = "/Gate_Drive/PWM_LIN2",
    gnd: str = "GND",
) -> PwmDrive:
    period = cfg.pwm_period_s
    pw = max(0.0, min(duty, 0.95)) * period

    def named(prefix: str, node: str, delay: float, active: bool) -> str:
        src = pulse_source(prefix, node, gnd, delay, pw if active else 0.0, period)
        return src.replace("VPWM ", f"{prefix} ", 1)

    if stage == StageMode.BUCK:
        return PwmDrive(
            hin1=named("VPWM_HIN1", hin1, 0, True),
            lin1=named("VPWM_LIN1", lin1, 0, False),
            hin2=named("VPWM_HIN2", hin2, 0, False),
            lin2=named("VPWM_LIN2", lin2, 0, True),
        )
    if stage == StageMode.BOOST:
        return PwmDrive(
            hin1=named("VPWM_HIN1", hin1, 0, False),
            lin1=named("VPWM_LIN1", lin1, 0, True),
            hin2=named("VPWM_HIN2", hin2, 0, True),
            lin2=named("VPWM_LIN2", lin2, 0, False),
        )

    half = period / 2.0
    return PwmDrive(
        hin1=named("VPWM_HIN1", hin1, 0, True),
        lin1=named("VPWM_LIN1", lin1, half, True),
        hin2=named("VPWM_HIN2", hin2, half, True),
        lin2=named("VPWM_LIN2", lin2, 0, True),
    )


def render_pwm_drive(drive: PwmDrive) -> str:
    return "\n".join([drive.hin1, drive.lin1, drive.hin2, drive.lin2])


def _pwl_points(
    t_start: float,
    t_end: float,
    duty: float,
    period: float,
    vhi: float,
    tr: float,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = [(t_start, 0.0)]
    pw = max(0.0, min(duty, 0.95)) * period
    if pw <= 0.0:
        points.append((t_end, 0.0))
        return points

    edge = min(tr, pw * 0.25, period * 0.1)
    t = t_start
    while t < t_end - 1e-15:
        t_on = t
        t_hi = min(t + edge, t_end)
        t_lo = min(t + pw - edge, t_end)
        t_off = min(t + pw, t_end)
        if t_hi > t_on:
            points.append((t_on, 0.0))
            points.append((t_hi, vhi))
        if t_lo > t_hi:
            points.append((t_lo, vhi))
        if t_off > t_lo:
            points.append((t_off, 0.0))
        t += period
    points.append((t_end, points[-1][1]))
    return _dedupe_pwl(points)


def _dedupe_pwl(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    points = sorted(points, key=lambda item: item[0])
    out: list[tuple[float, float]] = []
    for t, v in points:
        if t < 0:
            continue
        if out and t <= out[-1][0]:
            out[-1] = (out[-1][0], v)
        else:
            out.append((t, v))
    return out


def pwl_source(name: str, node: str, gnd: str, points: list[tuple[float, float]]) -> str:
    if not points:
        return f"{name} {node} {gnd} DC 0"
    flat = " ".join(f"{t:g} {v:g}" for t, v in points)
    return f"{name} {node} {gnd} PWL({flat})"


def timeline_to_pwl_drive(
    timeline: list[tuple[float, float, StageMode]],
    cfg: BoardConfig,
    t_end: float,
) -> PwmDrive:
    signals: dict[str, list[tuple[float, float]]] = {
        "hin1": [],
        "lin1": [],
        "hin2": [],
        "lin2": [],
    }
    tr = 100e-9
    period = cfg.pwm_period_s

    for idx, (t_start, duty, stage) in enumerate(timeline):
        t_stop = timeline[idx + 1][0] if idx + 1 < len(timeline) else t_end
        active = {
            "hin1": stage in (StageMode.BUCK, StageMode.BUCKBOOST),
            "lin2": stage in (StageMode.BUCK, StageMode.BUCKBOOST),
            "lin1": stage in (StageMode.BOOST, StageMode.BUCKBOOST),
            "hin2": stage in (StageMode.BOOST, StageMode.BUCKBOOST),
        }
        if stage == StageMode.BUCKBOOST:
            for key in signals:
                pts = _pwl_points(t_start, t_stop, duty if active[key] else 0.0, period, 3.3, tr)
                signals[key].extend(pts)
        else:
            signals["hin1"].extend(_pwl_points(t_start, t_stop, duty if active["hin1"] else 0.0, period, 3.3, tr))
            signals["lin1"].extend(_pwl_points(t_start, t_stop, duty if active["lin1"] else 0.0, period, 3.3, tr))
            signals["hin2"].extend(_pwl_points(t_start, t_stop, duty if active["hin2"] else 0.0, period, 3.3, tr))
            signals["lin2"].extend(_pwl_points(t_start, t_stop, duty if active["lin2"] else 0.0, period, 3.3, tr))

    return PwmDrive(
        hin1=pwl_source("VPWM_HIN1", "/Gate_Drive/PWM_HIN1", "GND", _dedupe_pwl(signals["hin1"])),
        lin1=pwl_source("VPWM_LIN1", "/Gate_Drive/PWM_LIN1", "GND", _dedupe_pwl(signals["lin1"])),
        hin2=pwl_source("VPWM_HIN2", "/Gate_Drive/PWM_HIN2", "GND", _dedupe_pwl(signals["hin2"])),
        lin2=pwl_source("VPWM_LIN2", "/Gate_Drive/PWM_LIN2", "GND", _dedupe_pwl(signals["lin2"])),
    )


def generate_mcu_pwm_lib(cfg: BoardConfig, path: Path) -> None:
    period_u = cfg.pwm_period_s * 1e6
    text = f"""* Auto-generated from board_config.h — do not edit by hand.
* Regenerate: python -m benchgate.cosim.codegen
*
* Parametric open-loop PWM shell (constant duty). Closed-loop uses benchgate sim cosim.
.subckt MCU_PWM_BUCK HIN1 LIN1 HIN2 LIN2 GND PARAMS: DUTY=0.5 FREQ={cfg.pwm_freq_hz:.0f}
.param PER={{1/FREQ}}
.param PW={{DUTY*PER}}
VPWM_HIN1 HIN1 GND PULSE(0 3.3 0 100n 100n {{PW}} {{PER}})
VPWM_LIN1 LIN1 GND DC 0
VPWM_HIN2 HIN2 GND DC 0
VPWM_LIN2 LIN2 GND PULSE(0 3.3 0 100n 100n {{PW}} {{PER}})
.ends MCU_PWM_BUCK

.subckt MCU_PWM_BOOST HIN1 LIN1 HIN2 LIN2 GND PARAMS: DUTY=0.5 FREQ={cfg.pwm_freq_hz:.0f}
.param PER={{1/FREQ}}
.param PW={{DUTY*PER}}
VPWM_HIN1 HIN1 GND DC 0
VPWM_LIN1 LIN1 GND PULSE(0 3.3 0 100n 100n {{PW}} {{PER}})
VPWM_HIN2 HIN2 GND PULSE(0 3.3 0 100n 100n {{PW}} {{PER}})
VPWM_LIN2 LIN2 GND DC 0
.ends MCU_PWM_BOOST

* PWM period = {period_u:.3f} us @ {cfg.pwm_freq_hz:.0f} Hz
* Control loop sample = {cfg.ctrl_loop_hz:.0f} Hz
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
