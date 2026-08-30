"""P2: lab.yaml thermal defaults, explicit gate evidence, watch (no hardware)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from benchgate.gate.report import build_gate_report
from benchgate.lab.store import LabDataStore
from benchgate.lab.thermal import apply_thermal_defaults, load_thermal_config
from benchgate.schemas import MappingManifest

from test_thermal_alert import FakeBench, FakeThermal, _frame


def _write_lab(design: Path, body: str) -> Path:
    models = design / "models"
    models.mkdir(parents=True, exist_ok=True)
    path = models / "lab.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _thermal_session(
    store: LabDataStore,
    *,
    captured_at: datetime,
    tags: list[str],
    derived: dict[str, float],
    session_id: str | None = None,
):
    return store.write_session(
        frames={"thermal": _frame(np.ones((4, 4)))},
        derived=derived,
        tags=tags,
        captured_at=captured_at,
        session_id=session_id,
    )


def test_lab_yaml_thermal_roundtrip(tmp_path):
    lab = tmp_path / "lab.yaml"
    lab.write_text(
        "\n".join(
            [
                "thermal:",
                "  homography_file: ~/.benchgate/config/thermal_map/f3ca8b951477.yaml",
                "  baseline_file: ~/.benchgate/config/thermal_baseline/f3ca8b951477.npz",
                "  delta_warn: 30",
                "  delta_fail: 50",
                "  session_tag: thermal-gate",
                "  frames: 3",
                "  reduce: median",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_thermal_config(lab)
    assert cfg["homography_file"].endswith("f3ca8b951477.yaml")
    assert "~" not in cfg["homography_file"]
    assert cfg["baseline_file"].endswith("f3ca8b951477.npz")
    assert cfg["delta_fail"] == 50
    filled = apply_thermal_defaults({"design_dir": "x", "frames": None}, cfg)
    assert filled["frames"] == 3
    assert filled["reduce"] == "median"
    kept = apply_thermal_defaults({"frames": 1, "reduce": "max"}, cfg)
    assert kept["frames"] == 1
    assert kept["reduce"] == "max"
    assert load_thermal_config(tmp_path / "missing.yaml") == {}


def test_thermal_evidence_requires_spec(tmp_path):
    captured = tmp_path / "models" / "captured"
    store = LabDataStore(captured)
    t1 = datetime(2026, 8, 30, tzinfo=timezone.utc)
    _thermal_session(
        store,
        captured_at=t1,
        tags=[],
        derived={"t_delta_peak": 99.0, "alert_severity_code": 2.0},
    )
    report = build_gate_report(
        MappingManifest(),
        captured_dir=captured,
        design_dir=tmp_path,
    )
    assert report.summary.get("thermal") is None


def test_thermal_evidence_by_session_id(tmp_path):
    captured = tmp_path / "models" / "captured"
    store = LabDataStore(captured)
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    old = _thermal_session(
        store,
        captured_at=t0,
        tags=["thermal-gate"],
        derived={"t_delta_peak": 11.0, "alert_severity_code": 0.0, "t_ref": 3.0},
        session_id="old_thermal",
    )
    _thermal_session(
        store,
        captured_at=t0 + timedelta(hours=2),
        tags=[],
        derived={"t_delta_peak": 99.0, "alert_severity_code": 2.0},
        session_id="new_thermal",
    )
    _write_lab(tmp_path, "thermal:\n  session_id: old_thermal\n")
    report = build_gate_report(
        MappingManifest(),
        captured_dir=captured,
        design_dir=tmp_path,
    )
    thermal = report.summary["thermal"]
    assert thermal["session_id"] == old.session_id
    assert thermal["t_delta_peak"] == 11.0
    assert thermal["alert_severity_code"] == 0.0
    assert thermal["t_ref"] == 3.0


def test_thermal_evidence_by_tag(tmp_path):
    captured = tmp_path / "models" / "captured"
    store = LabDataStore(captured)
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    tagged = _thermal_session(
        store,
        captured_at=t0,
        tags=["thermal-gate"],
        derived={"t_delta_peak": 12.0, "alert_severity_code": 1.0},
        session_id="tagged_thermal",
    )
    _thermal_session(
        store,
        captured_at=t0 + timedelta(hours=3),
        tags=[],
        derived={"t_delta_peak": 99.0, "alert_severity_code": 2.0},
        session_id="newer_unspecified",
    )
    _write_lab(tmp_path, "thermal:\n  session_tag: thermal-gate\n")
    report = build_gate_report(
        MappingManifest(),
        captured_dir=captured,
        design_dir=tmp_path,
    )
    thermal = report.summary["thermal"]
    assert thermal["session_id"] == tagged.session_id
    assert thermal["t_delta_peak"] == 12.0


def test_alert_fills_homography_from_lab_yaml(tmp_path, monkeypatch):
    from benchgate.agent.dispatch import dispatch
    from benchgate.lab.board_map import FootprintBox, SchematicPart

    captured = tmp_path / "models" / "captured"
    store = LabDataStore(captured)
    values = np.zeros((16, 16))
    values[2:5, 2:5] = 50.0
    meta = store.write_session(frames={"thermal": _frame(values)}, derived={"t_max": 50.0})
    hom = tmp_path / "map.yaml"
    hom.write_text(
        "\n".join(
            [
                "pairs:",
                '  - "0,0:0,0"',
                '  - "15,0:15,0"',
                '  - "15,15:15,15"',
                '  - "0,15:0,15"',
            ]
        ),
        encoding="utf-8",
    )
    _write_lab(
        tmp_path,
        f"thermal:\n  homography_file: {hom}\n  delta_fail: 20\n",
    )
    fps = [FootprintBox("U1", 3.0, 3.0, 4.0, 4.0)]
    sch = {"U1": SchematicPart("U1", "Device:U", "MCU")}
    monkeypatch.setattr("benchgate.lab.board_map.load_pcb_footprints", lambda d: fps)
    monkeypatch.setattr("benchgate.lab.board_map.load_schematic_index", lambda d: sch)
    monkeypatch.setattr("benchgate.lab.board_map.load_board_outline", lambda d: (-1.0, -1.0, 20.0, 20.0))
    result = dispatch(
        "lab_thermal_alert",
        {
            "design_dir": str(tmp_path),
            "session_id": meta.session_id,
            "require_baseline": False,
        },
    )
    assert result["regions"]
    assert result["regions"][0]["kicad_hits"]
    assert result["regions"][0]["kicad_hits"][0]["reference"] == "U1"


def test_baseline_ignores_capture_frames(tmp_path, monkeypatch):
    """thermal.frames sizes a capture burst; a 3-frame baseline sigma is noise."""
    from benchgate.agent.dispatch import dispatch

    monkeypatch.setenv("BENCHGATE_HOME", str(tmp_path / "home"))
    _write_lab(tmp_path, "thermal:\n  frames: 3\n")
    inst = FakeThermal(np.full((4, 4), 3000.0))
    monkeypatch.setattr("benchgate.agent.dispatch._open_bench", lambda p, a: FakeBench(inst))
    result = dispatch("lab_thermal_baseline", {"design_dir": str(tmp_path)})
    assert result["n_frames"] == 16

    _write_lab(tmp_path, "thermal:\n  frames: 3\n  baseline_frames: 24\n")
    result = dispatch("lab_thermal_baseline", {"design_dir": str(tmp_path)})
    assert result["n_frames"] == 24


def test_thermal_watch_reports_failed_iterations(tmp_path, monkeypatch):
    """A poll that never captured must not look like a clean run."""
    import argparse

    from benchgate.cli import cmd_lab_thermal_watch

    _write_lab(tmp_path, "thermal:\n  delta_fail: 20\n")

    def _boom(paths, args):
        raise RuntimeError("thermal camera unplugged")

    monkeypatch.setattr("benchgate.agent.dispatch._open_bench", _boom)
    args = argparse.Namespace(
        design=str(tmp_path),
        instrument=None,
        interval_s=0,
        max_iterations=2,
        quiet=True,
        frames=None,
        reduce=None,
        baseline_file=None,
        delta_warn=None,
        delta_fail=None,
        k_sigma_warn=None,
        k_sigma_fail=None,
        no_require_baseline=True,
        homography_file=None,
        apply_calibration=False,
    )
    assert cmd_lab_thermal_watch(args) == 1


def _watch(design: Path, values: np.ndarray, monkeypatch):
    from benchgate.agent.dispatch import dispatch

    inst = FakeThermal(values)
    monkeypatch.setattr("benchgate.agent.dispatch._open_bench", lambda p, a: FakeBench(inst))
    return dispatch(
        "lab_thermal_watch",
        {
            "design_dir": str(design),
            "max_iterations": 2,
            "interval_s": 0,
            "quiet": True,
            "require_baseline": False,
        },
    )


def test_thermal_watch_two_iterations(tmp_path, monkeypatch):
    _write_lab(tmp_path, "thermal:\n  delta_fail: 20\n  session_tag: thermal-gate\n")
    result = _watch(tmp_path, np.full((8, 8), 10.0), monkeypatch)
    assert result["iterations"] == 2
    assert len(result["runs"]) == 2
    assert all(r["ok"] for r in result["runs"])


def test_thermal_watch_keeps_only_alerting_polls(tmp_path, monkeypatch):
    """A 30 s poll must not leave a session behind every tick."""
    _write_lab(tmp_path, "thermal:\n  delta_fail: 20\n  session_tag: thermal-gate\n")
    store = LabDataStore(tmp_path / "models" / "captured")

    clean = _watch(tmp_path, np.full((8, 8), 10.0), monkeypatch)
    assert all(r["severity"] == "none" and r["session_id"] is None for r in clean["runs"])
    assert store.list_sessions(tags=["thermal-gate"]) == []

    hot = np.full((8, 8), 10.0)
    hot[2:5, 2:5] = 100.0
    alerting = _watch(tmp_path, hot, monkeypatch)
    assert all(r["severity"] == "fail" for r in alerting["runs"])
    tagged = store.list_sessions(tags=["thermal-gate"])
    assert len(tagged) == 2
    assert tagged[0].derived["alert_severity_code"] == 2.0
    assert (tagged[0].path / "thermal_alert.json").is_file()
