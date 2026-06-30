"""Tests for firmware board_config parsing and cosim helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchgate.cosim.board_config import divider_ratio_from_netlist, load_board_config
from benchgate.cosim.control_native import build_control_library, load_control_sim
from benchgate.cosim.pwm_drive import StageMode, drive_for_stage, resolve_stage


REPO_ROOT = Path(__file__).resolve().parents[2]
FIRMWARE = REPO_ROOT / "dcdc/h-bridge/h-bridge-pcb/firmware"


@pytest.mark.skipif(not (FIRMWARE / "Inc/board_config.h").exists(), reason="firmware tree missing")
def test_load_board_config() -> None:
    cfg = load_board_config(FIRMWARE / "Inc/board_config.h")
    assert cfg.pwm_freq_hz == 200_000
    assert cfg.ctrl_loop_hz == 20_000
    assert cfg.vin_div_ratio == pytest.approx(11.0)
    assert cfg.vout_div_ratio == pytest.approx((560.0 + 91.0) / 91.0)


def test_divider_from_netlist() -> None:
    netlist = """
R63 VIN_PORT /Sense_&_Control/ADC_VIN 100k
R64 /Sense_&_Control/ADC_VIN GND 10k
R65 /H-Bridge_Power/VOUT /Sense_&_Control/ADC_VOUT 100k
R66 /Sense_&_Control/ADC_VOUT GND 91k
"""
    vin = divider_ratio_from_netlist(netlist, "/Sense_&_Control/ADC_VIN", "VIN_PORT")
    vout = divider_ratio_from_netlist(netlist, "/Sense_&_Control/ADC_VOUT", "/H-Bridge_Power/VOUT")
    assert vin == pytest.approx(11.0)
    assert vout == pytest.approx(191.0 / 91.0)


def test_resolve_stage_buck() -> None:
    assert resolve_stage(12.0, 5.0, StageMode.AUTO) == StageMode.BUCK


@pytest.mark.skipif(not (FIRMWARE / "Src/control.c").exists(), reason="firmware tree missing")
def test_control_native_build_and_step(tmp_path: Path) -> None:
    lib = build_control_library(FIRMWARE, tmp_path)
    assert lib.exists()
    sim = load_control_sim(FIRMWARE, tmp_path)
    sim.init(v_set_v=5.0, mode=0, enable=True)
    duty = sim.update(12.0, 2.5, 0.0)
    assert 0.0 <= duty <= 0.95


@pytest.mark.skipif(not (FIRMWARE / "Src/control.c").exists(), reason="firmware tree missing")
def test_cv_sim_gains_raise_duty(tmp_path: Path) -> None:
    """MODE_CV cascade needs boosted inner-loop gains for cosim plant."""
    sim = load_control_sim(FIRMWARE, tmp_path, sim_gains="cv")
    sim.init(v_set_v=5.0, mode=0, enable=True)
    duties = []
    for _ in range(100):
        duties.append(sim.update(12.0, 1.7, 0.17))
    assert max(duties) > 0.05
    assert max(duties) < 0.85


def test_drive_for_stage_buck() -> None:
    cfg = load_board_config(FIRMWARE / "Inc/board_config.h") if (FIRMWARE / "Inc/board_config.h").exists() else None
    if cfg is None:
        pytest.skip("firmware tree missing")
    drive = drive_for_stage(StageMode.BUCK, 0.4, cfg)
    assert "VPWM_HIN1" in drive.hin1
    assert "VPWM_LIN2" in drive.lin2
    assert "DC 0" in drive.lin1
