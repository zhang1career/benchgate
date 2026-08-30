"""P1 thermal alert + P0 leftover capture-calibration tests (no hardware)."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from benchgate.instruments.types import Frame2D, Frame2DSeries, QuantityKind
from benchgate.lab.store import LabDataStore
from benchgate.lab.thermal import ThermalCalibration, apply_calibration
from benchgate.lab.thermal_alert import AlertPolicy, evaluate_alert


def _frame(values, unit="count", mask=None, calibration=None):
    return Frame2D(
        values=np.asarray(values, dtype=float),
        unit=unit,
        quantity=QuantityKind.TEMPERATURE,
        timestamp=datetime.now(timezone.utc),
        mask=None if mask is None else np.asarray(mask, dtype=bool),
        calibration=calibration,
    )


class FakeThermal:
    def __init__(self, values: np.ndarray):
        self._values = np.asarray(values, dtype=float)
        self.disconnected = False

    def identify(self) -> str:
        return "fake-thermal"

    def capture_frame(self) -> Frame2D:
        return _frame(self._values)

    def capture_burst(self, n: int) -> Frame2DSeries:
        stack = np.repeat(self._values[np.newaxis, ...], int(n), axis=0)
        return Frame2DSeries(
            t_rel_s=np.arange(int(n), dtype=float),
            values=stack,
            unit="count",
            quantity=QuantityKind.TEMPERATURE,
            t0_utc=datetime.now(timezone.utc),
        )

    def get_emissivity(self) -> float:
        return 1.0

    def disconnect(self) -> None:
        self.disconnected = True


class FakeBench:
    def __init__(self, inst: FakeThermal):
        self._inst = inst

    def select(self, role=None, instrument=None):
        return self._inst

    def instrument_for_role(self, role):
        return "pico_thermal"


def test_alert_none_on_flat_frame():
    result = evaluate_alert(
        _frame(np.full((8, 8), 3000.0)),
        policy=AlertPolicy(delta_fail=20.0, require_baseline=False),
    )
    assert result.severity == "none"
    assert result.regions == []


def test_alert_detects_synthetic_blob():
    grid = np.zeros((8, 8))
    grid[2:5, 2:5] = 50.0
    result = evaluate_alert(
        _frame(grid),
        policy=AlertPolicy(delta_fail=20.0, require_baseline=False),
    )
    assert result.severity == "fail"
    assert len(result.regions) == 1
    assert result.regions[0].centroid_x == pytest.approx(3.0)
    assert result.regions[0].centroid_y == pytest.approx(3.0)


def test_alert_baseline_removes_fixed_pattern():
    baseline = np.full((8, 8), 3000.0)
    baseline[:, 0] = 3100.0
    frame = baseline.copy()
    frame[3:6, 4:7] = 3050.0
    result = evaluate_alert(
        _frame(frame),
        baseline=baseline,
        policy=AlertPolicy(delta_fail=20.0, require_baseline=True),
    )
    assert result.severity == "fail"
    assert len(result.regions) == 1
    assert result.regions[0].peak_col != 0
    assert 4 <= result.regions[0].peak_col <= 6
    assert 3 <= result.regions[0].peak_row <= 5


def test_alert_k_sigma_threshold():
    baseline = np.full((8, 8), 3000.0)
    sigma = np.full((8, 8), 2.0)
    frame = baseline.copy()
    frame[3:6, 3:6] = 3030.0
    result = evaluate_alert(
        _frame(frame),
        baseline=baseline,
        sigma=sigma,
        policy=AlertPolicy(k_sigma_fail=5.0, require_baseline=True),
    )
    assert result.policy_source == "k_sigma"
    assert result.threshold_fail == pytest.approx(10.0)
    assert result.severity == "fail"


def test_alert_missing_threshold_raises():
    with pytest.raises(ValueError, match="delta_warn|k_sigma"):
        evaluate_alert(_frame(np.zeros((4, 4))), policy=AlertPolicy(require_baseline=False))


def test_alert_unit_mismatch_raises():
    raw = _frame(np.full((4, 4), 3000.0))
    degc = apply_calibration(raw, ThermalCalibration(kind="affine2pt", slope=0.1, offset=-273.15))
    with pytest.raises(ValueError, match="unit"):
        evaluate_alert(
            degc,
            baseline=np.zeros((4, 4)),
            baseline_unit="count",
            policy=AlertPolicy(delta_fail=1.0),
        )


def test_alert_regions_sorted_and_capped():
    grid = np.zeros((16, 16))
    grid[1:3, 1:3] = 80.0
    grid[1:3, 12:14] = 50.0
    grid[12:14, 1:3] = 30.0
    result = evaluate_alert(
        _frame(grid),
        policy=AlertPolicy(delta_fail=10.0, max_regions=2, require_baseline=False),
    )
    assert len(result.regions) == 2
    assert result.regions[0].peak_delta >= result.regions[1].peak_delta
    assert result.regions[0].peak_delta == pytest.approx(80.0)


def test_alert_maps_each_region_separately(tmp_path, monkeypatch):
    from benchgate.agent.dispatch import dispatch
    from benchgate.lab.board_map import FootprintBox, SchematicPart

    values = np.zeros((16, 16))
    values[2:5, 2:5] = 50.0
    values[10:13, 10:13] = 40.0
    frame = _frame(values)
    store = LabDataStore(tmp_path / "models" / "captured")
    meta = store.write_session(frames={"thermal": frame}, derived={"t_max": 50.0})
    fps = [
        FootprintBox("U1", 3.0, 3.0, 4.0, 4.0),
        FootprintBox("U2", 11.0, 11.0, 4.0, 4.0),
    ]
    sch = {
        "U1": SchematicPart("U1", "Device:U", "MCU"),
        "U2": SchematicPart("U2", "Device:U", "LDO"),
    }
    monkeypatch.setattr("benchgate.lab.board_map.load_pcb_footprints", lambda d: fps)
    monkeypatch.setattr("benchgate.lab.board_map.load_schematic_index", lambda d: sch)
    monkeypatch.setattr("benchgate.lab.board_map.load_board_outline", lambda d: (-1.0, -1.0, 20.0, 20.0))
    result = dispatch(
        "lab_thermal_alert",
        {
            "design_dir": str(tmp_path),
            "session_id": meta.session_id,
            "require_baseline": False,
            "delta_fail": 20.0,
            "homography": ["0,0:0,0", "15,0:15,0", "15,15:15,15", "0,15:0,15"],
        },
    )
    assert len(result["regions"]) == 2
    refs = {r["kicad_hits"][0]["reference"] for r in result["regions"]}
    assert refs == {"U1", "U2"}
    artifact = meta.path / "thermal_alert.json"
    assert artifact.is_file()
    reloaded = store.get_session(meta.session_id)
    assert reloaded.derived["alert_region_count"] == 2.0
    assert reloaded.derived["alert_severity_code"] == 2.0


def test_capture_apply_calibration_writes_degc(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHGATE_HOME", str(tmp_path / "home"))
    from benchgate.agent.dispatch import dispatch
    from benchgate.lab.thermal import calibration_path, save_calibration

    save_calibration(
        ThermalCalibration(kind="affine2pt", slope=0.1, offset=-273.15, instrument_idn="fake-thermal"),
        calibration_path("fake-thermal"),
    )
    inst = FakeThermal(np.full((4, 4), 3000.0))
    monkeypatch.setattr("benchgate.agent.dispatch._open_bench", lambda p, a: FakeBench(inst))
    result = dispatch("lab_thermal_capture", {"design_dir": str(tmp_path), "apply_calibration": True})
    assert result["unit"] == "degC"
    store = LabDataStore(tmp_path / "models" / "captured")
    extra = store.get_session(result["session_id"]).channel("thermal").extra
    assert extra["calibration_slope"] == pytest.approx(0.1)
    assert extra["unit"] == "degC"


def test_capture_apply_calibration_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHGATE_HOME", str(tmp_path / "home"))
    from benchgate.agent.dispatch import dispatch

    inst = FakeThermal(np.ones((4, 4)))
    monkeypatch.setattr("benchgate.agent.dispatch._open_bench", lambda p, a: FakeBench(inst))
    with pytest.raises(FileNotFoundError, match="calibration"):
        dispatch("lab_thermal_capture", {"design_dir": str(tmp_path), "apply_calibration": True})
