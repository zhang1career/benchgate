"""Closed-loop cosim: firmware control.c + ngspice plant (fixed-point PWL iteration)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from benchgate.kicad.cli_export import export_spice_netlist
from benchgate.kicad.project import KiCadProject
from benchgate.cosim.board_config import BoardConfig, divider_ratio_from_netlist, load_board_config
from benchgate.cosim.control_native import load_control_sim
from benchgate.cosim.sensing import read_iout_a
from benchgate.cosim.pwm_drive import (
    StageMode,
    drive_four_switch_complementary,
    render_pwm_drive,
    resolve_stage,
)
from benchgate.sim.analysis import SimCheckReport, analyze_raw_file, load_profile_checks, parse_ngspice_raw
from benchgate.sim.netlist import format_rload, prepare_netlist
from benchgate.sim.runner import SimResult, run_ngspice

_PWM_SOURCE_RE = re.compile(r"^VPWM_(HIN1|LIN1|HIN2|LIN2)\s", re.MULTILINE)
_V1_RE = re.compile(r"^V1\s", re.MULTILINE)
_RLOAD_RE = re.compile(r"^RLOAD\s", re.MULTILINE)


@dataclass
class CosimConfig:
    firmware_dir: Path
    t_end_s: float = 5e-3
    v_set_v: float = 5.0
    i_set_a: float = 1.0
    mode: int = 1
    stage: int = 0
    enable: bool = True
    max_iterations: int = 4
    duty_tol: float = 0.02
    initial_duty: float = 0.45
    pwm_period_s: float | None = None
    rload_ohm: float = 10.0
    estimate_iout_from_vout: bool = True
    sim_gains: str = "cc"
    prefer_iout_estimate: bool = False
    tran_step_s: float = 0.1e-6


@dataclass
class CosimStep:
    t_s: float
    duty: float
    stage: str
    vin_v: float
    vout_v: float
    iout_a: float
    iout_source: str = ""


@dataclass
class CosimReport:
    success: bool
    steps: int
    iterations: int
    final_vout_v: float
    final_duty: float
    prepared_netlist: str
    log_path: str | None
    raw_output: str | None
    ran_at: str
    ngspice_ok: bool = True
    checks: dict | None = None
    timeline_path: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def load_cosim_config(
    profile_path: Path,
    profile: str,
    firmware_default: Path,
    *,
    design_dir: Path,
) -> CosimConfig:
    data = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    block = data.get(profile) or {}
    cosim = block.get("cosim") or {}
    firmware = Path(cosim.get("firmware_dir", firmware_default))
    if not firmware.is_absolute():
        firmware = (design_dir / firmware).resolve()
    return CosimConfig(
        firmware_dir=firmware,
        t_end_s=_parse_time(cosim.get("t_end", "5ms")),
        v_set_v=float(cosim.get("v_set_v", 5.0)),
        i_set_a=float(cosim.get("i_set_a", 1.0)),
        mode=int(cosim.get("mode", 1)),
        stage=int(cosim.get("stage", 0)),
        enable=bool(cosim.get("enable", True)),
        max_iterations=int(cosim.get("max_iterations", 4)),
        duty_tol=float(cosim.get("duty_tol", 0.02)),
        initial_duty=float(cosim.get("initial_duty", 0.45)),
        pwm_period_s=_optional_float(cosim.get("pwm_period_s")),
        rload_ohm=float(cosim.get("rload_ohm", 10.0)),
        estimate_iout_from_vout=bool(cosim.get("estimate_iout_from_vout", True)),
        sim_gains=str(cosim.get("sim_gains", "cc")),
        prefer_iout_estimate=bool(cosim.get("prefer_iout_estimate", False)),
        tran_step_s=_parse_time(cosim.get("tran_step", "0.1u")),
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _parse_time(value: str | float | int) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    scales = {"ms": 1e-3, "us": 1e-6, "u": 1e-6, "s": 1.0}
    for suffix, scale in scales.items():
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * scale
    return float(text)


def _strip_open_loop_stimulus(text: str) -> str:
    text = _PWM_SOURCE_RE.sub("", text)
    text = _V1_RE.sub("", text)
    text = _RLOAD_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def _inject_stimulus(text: str, pwm_block: str, vin_v: float = 12.0, rload_ohm: float = 10.0) -> str:
    insert = (
        f"V1 VIN_PORT GND DC {vin_v:g}\n"
        f"{format_rload(rload_ohm)}\n"
        f"{pwm_block.strip()}\n"
    )
    if ".control" in text:
        return text.replace(".control", insert + ".control", 1)
    return text.rstrip() + "\n" + insert


def _interp(time: np.ndarray, series: np.ndarray, t_query: float) -> float:
    if time.size == 0:
        return float("nan")
    return float(np.interp(t_query, time, series))


def _build_cir(
    base_text: str,
    pwm_block: str,
    t_end_s: float,
    *,
    tran_step_s: float,
) -> str:
    text = _strip_open_loop_stimulus(base_text)
    text = _inject_stimulus(text, pwm_block)
    text = re.sub(
        r"tran\s+[^\n]+",
        f"tran {tran_step_s:g} {t_end_s:g}",
        text,
        count=1,
    )
    return text


def _raw_covers_window(time: np.ndarray, t_end_s: float, *, min_fraction: float = 0.95) -> bool:
    return time.size > 0 and float(time[-1]) >= t_end_s * min_fraction


def _iterate_control(
    control,
    time: np.ndarray,
    signals: dict[str, np.ndarray],
    *,
    n_steps: int,
    dt_s: float,
    stage_mode: StageMode,
    shunt: float,
    gain: float,
    rload_ohm: float,
    prefer_iout_estimate: bool,
) -> tuple[list[CosimStep], list[tuple[float, float, StageMode]]]:
    vin = signals.get("v(vin_port)")
    vout = signals.get("v(/h-bridge_power/vout)")
    if vin is None or vout is None:
        raise KeyError("plant raw output missing vin_port or vout")

    steps: list[CosimStep] = []
    timeline: list[tuple[float, float, StageMode]] = []
    stage = stage_mode

    for k in range(n_steps):
        t_s = k * dt_s
        t_sample = min((k + 1) * dt_s - dt_s / 2.0, time[-1])
        vin_v = _interp(time, vin, t_sample)
        vout_v = _interp(time, vout, t_sample)
        iout_a, iout_src = read_iout_a(
            time,
            signals,
            t_sample,
            shunt_ohm=shunt,
            gain_vv=gain,
            vout_v=vout_v,
            rload_ohm=rload_ohm,
            prefer_estimate=prefer_iout_estimate,
        )
        stage = resolve_stage(vin_v, vout_v, stage_mode)
        duty = control.update(vin_v, vout_v, iout_a)
        steps.append(
            CosimStep(
                t_s=t_s,
                duty=duty,
                stage=stage.name,
                vin_v=vin_v,
                vout_v=vout_v,
                iout_a=iout_a,
                iout_source=iout_src,
            )
        )
        timeline.append((t_s, duty, stage))
    return steps, timeline


def run_cosim(
    design_dir: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    sim_profile_path: Path,
    profile: str = "hbridge_pwm_closed",
    build_dir: Path | None = None,
) -> tuple[CosimReport, SimResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if build_dir is None:
        from benchgate.paths import benchgate_home

        build_dir = benchgate_home() / "cosim"
    cosim_cfg = load_cosim_config(
        sim_profile_path,
        profile,
        design_dir / "firmware",
        design_dir=design_dir,
    )
    board_cfg = load_board_config(cosim_cfg.firmware_dir / "Inc" / "board_config.h")

    project = KiCadProject.load(design_dir)
    exported = output_dir / "exported.net"
    base_prepared = output_dir / "prepared_open.cir"
    export_spice_netlist(project.schematic, exported)
    prepare_netlist(
        exported,
        manifest_path,
        base_prepared,
        sim_profile_path=sim_profile_path,
        profile=profile,
    )
    base_text = base_prepared.read_text(encoding="utf-8")

    control = load_control_sim(
        cosim_cfg.firmware_dir,
        build_dir=build_dir,
        sim_gains=cosim_cfg.sim_gains,
    )
    dt_s = board_cfg.ctrl_dt_s
    n_steps = max(1, int(cosim_cfg.t_end_s / dt_s))
    pwm_period = cosim_cfg.pwm_period_s or board_cfg.pwm_period_s
    duty_scalar = cosim_cfg.initial_duty
    duty_trace = np.full(n_steps, duty_scalar, dtype=float)

    steps: list[CosimStep] = []
    iterations = 0
    result: SimResult | None = None

    for iteration in range(cosim_cfg.max_iterations):
        iterations = iteration + 1
        drive = drive_four_switch_complementary(duty_scalar, board_cfg, period_s=pwm_period)
        cir_text = _build_cir(
            base_text,
            render_pwm_drive(drive),
            cosim_cfg.t_end_s,
            tran_step_s=cosim_cfg.tran_step_s,
        )
        prepared = output_dir / "prepared.cir"
        prepared.write_text(cir_text, encoding="utf-8")
        result = run_ngspice(prepared, work_dir=output_dir)
        if not result.raw_output:
            break

        time, signals = parse_ngspice_raw(result.raw_output)
        if not result.success and not _raw_covers_window(time, cosim_cfg.t_end_s):
            break
        control.init(
            mode=cosim_cfg.mode,
            stage=cosim_cfg.stage,
            v_set_v=cosim_cfg.v_set_v,
            i_set_a=cosim_cfg.i_set_a,
            enable=cosim_cfg.enable,
        )
        steps, _timeline = _iterate_control(
            control,
            time,
            signals,
            n_steps=n_steps,
            dt_s=dt_s,
            stage_mode=StageMode(cosim_cfg.stage),
            shunt=board_cfg.isense_shunt_ohm,
            gain=board_cfg.isense_gain_vv,
            rload_ohm=cosim_cfg.rload_ohm,
            prefer_iout_estimate=cosim_cfg.prefer_iout_estimate or cosim_cfg.estimate_iout_from_vout,
        )
        duty_trace = np.array([s.duty for s in steps], dtype=float)
        new_scalar = float(np.clip(np.mean(duty_trace[-max(1, n_steps // 10):]), 0.0, 0.95))
        delta = abs(new_scalar - duty_scalar)
        duty_scalar = new_scalar
        if delta < cosim_cfg.duty_tol:
            break

    timeline_path = output_dir / "cosim_timeline.json"
    timeline_path.write_text(json.dumps([asdict(s) for s in steps], indent=2), encoding="utf-8")

    check_report: SimCheckReport | None = None
    checks = load_profile_checks(sim_profile_path, profile)
    if result and checks and result.raw_output:
        check_report = analyze_raw_file(result.raw_output, checks)

    final_vout = steps[-1].vout_v if steps else float("nan")
    report = CosimReport(
        success=bool(result and result.success and (check_report.passed if check_report else True)),
        steps=len(steps),
        iterations=iterations,
        final_vout_v=final_vout,
        final_duty=steps[-1].duty if steps else 0.0,
        prepared_netlist=str(output_dir / "prepared.cir"),
        log_path=str(result.log_path) if result and result.log_path else None,
        raw_output=str(result.raw_output) if result and result.raw_output else None,
        ran_at=datetime.now(timezone.utc).isoformat(),
        ngspice_ok=bool(result and result.success),
        checks=check_report.to_dict() if check_report else None,
        timeline_path=str(timeline_path),
    )
    (output_dir / "cosim_report.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report, result or SimResult(False, "", "", None, None)
