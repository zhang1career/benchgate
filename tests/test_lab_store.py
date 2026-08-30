"""Tests for the S0 session store (write / query / load round-trips)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from benchgate.instruments.types import Frame2D, QuantityKind, ScalarSeries, Waveform
from benchgate.lab.store import ChannelKind, LabDataStore, new_session_id, register_channel_kind


def _waveform(t0):
    t = np.linspace(0, 1e-3, 50)
    v = np.exp(-t / 2e-4) * 3.3
    return Waveform(time_s=t, voltage_v=v, channel=1, timestamp=t0, sample_rate_hz=1e6)


def _series(t0):
    return ScalarSeries(
        t_rel_s=np.array([0.0, 0.2, 0.4]),
        values=np.array([3.28, 3.29, 3.30]),
        unit="V",
        quantity=QuantityKind.VOLTAGE,
        t0_utc=t0,
        flags=[{"dc": True}, {"dc": True}, {"dc": True}],
    )


def test_write_and_load_session(tmp_path):
    store = LabDataStore(tmp_path / "captured")
    t0 = datetime.now(timezone.utc)
    meta = store.write_session(
        component_ref="C1",
        mpn="100n",
        kicad_key="Device:C::100n",
        design="design/myboard",
        waveforms={"scope_ch1": _waveform(t0)},
        scalar_series={"dmm": _series(t0)},
        derived={"tau_s": 2e-4, "dmm_steady": 3.29},
        roles={"scope": "scope_main", "dmm": "dmm_bench", "awg": None},
        instruments={"scope_idn": "RIGOL"},
        tags=["rc_step"],
    )

    # session.yaml + payloads exist
    assert (meta.path / "session.yaml").is_file()
    assert (meta.path / "scope_ch1.npz").is_file()
    assert (meta.path / "dmm.csv").is_file()
    assert (meta.path / "derived.json").is_file()

    reloaded = store.get_session(meta.session_id)
    assert reloaded.component_ref == "C1"
    assert reloaded.derived["tau_s"] == 2e-4
    assert reloaded.channel("scope_ch1").kind == "waveform"


def test_load_waveform_window(tmp_path):
    store = LabDataStore(tmp_path / "captured")
    t0 = datetime.now(timezone.utc)
    meta = store.write_session(component_ref="C1", waveforms={"scope_ch1": _waveform(t0)})

    full = store.load_waveform(meta.session_id, "scope_ch1")
    assert len(full) == 50

    cropped = store.load_waveform(meta.session_id, "scope_ch1", t_start=0.0, t_end=5e-4)
    assert len(cropped) < 50
    assert cropped.time_s.max() <= 5e-4 + 1e-12


def test_load_scalar_series(tmp_path):
    store = LabDataStore(tmp_path / "captured")
    t0 = datetime.now(timezone.utc)
    meta = store.write_session(component_ref="C1", scalar_series={"dmm": _series(t0)})
    s = store.load_scalar_series(meta.session_id, "dmm")
    assert s.unit == "V"
    assert len(s) == 3
    assert s.values[0] == 3.28
    assert s.flags[0].get("dc") is True


def test_list_and_metric_series(tmp_path):
    store = LabDataStore(tmp_path / "captured")
    base = datetime.now(timezone.utc)
    for i in range(3):
        ts = base + timedelta(minutes=i)
        store.write_session(
            component_ref="C1",
            captured_at=ts,
            session_id=new_session_id(ts),
            derived={"tau_s": 2e-4 + i * 1e-5},
        )
    store.write_session(component_ref="C2", derived={"tau_s": 9e-4})

    c1 = store.list_sessions(component_ref="C1")
    assert len(c1) == 3
    # ordered by capture time
    assert c1[0].captured_at <= c1[-1].captured_at

    series = store.metric_series("tau_s", component_ref="C1")
    assert [round(r["value"], 6) for r in series] == [2e-4, 2.1e-4, 2.2e-4]


def test_write_and_load_frame2d(tmp_path):
    store = LabDataStore(tmp_path / "captured")
    t0 = datetime.now(timezone.utc)
    frame = Frame2D(
        values=np.arange(16, dtype=float).reshape(4, 4),
        unit="count",
        quantity=QuantityKind.TEMPERATURE,
        timestamp=t0,
        metadata={"fixture_id": "abc123abc123"},
    )
    meta = store.write_session(
        frames={"thermal": frame},
        derived={"t_max": 15.0, "frame_unit_is_degc": 0.0},
        tags=["fixture:abc123abc123"],
    )
    assert meta.channel("thermal").kind == "frame2d"
    loaded = store.load_frame2d(meta.session_id, "thermal")
    assert loaded.unit == "count"
    assert loaded.values[3, 3] == 15
    assert loaded.metadata.get("fixture_id") == "abc123abc123"


def test_write_and_load_frame2d_keeps_calibration(tmp_path):
    from benchgate.lab.thermal import ThermalCalibration, apply_calibration

    store = LabDataStore(tmp_path / "captured")
    t0 = datetime.now(timezone.utc)
    raw = Frame2D(
        values=np.full((4, 4), 3000.0),
        unit="count",
        quantity=QuantityKind.TEMPERATURE,
        timestamp=t0,
    )
    degc = apply_calibration(raw, ThermalCalibration(kind="affine2pt", slope=0.1, offset=-273.15))
    meta = store.write_session(frames={"thermal": degc})
    extra = meta.channel("thermal").extra
    assert extra["calibration_slope"] == pytest.approx(0.1)
    assert extra["calibration_offset"] == pytest.approx(-273.15)
    loaded = store.load_frame2d(meta.session_id, "thermal")
    assert loaded.unit == "degC"
    assert loaded.calibration["slope"] == pytest.approx(0.1)
    assert loaded.calibration["offset"] == pytest.approx(-273.15)
    assert loaded.values[0, 0] == pytest.approx(26.85)


def test_register_custom_channel_kind(tmp_path):
    def write(sdir, name, payload):
        path = sdir / f"{name}.txt"
        path.write_text(str(payload), encoding="utf-8")
        from benchgate.lab.store import ChannelMeta

        return ChannelMeta(name=name, kind="probe", path=path.name)

    def load(sdir, ch, meta):
        return (sdir / ch.path).read_text(encoding="utf-8")

    register_channel_kind(ChannelKind("probe", write, load))
    try:
        store = LabDataStore(tmp_path / "captured")
        meta = store.write_session(payloads={"note": ("probe", "hello")})
        assert meta.channel("note").kind == "probe"
        from benchgate.lab.store import CHANNEL_KINDS

        assert CHANNEL_KINDS["probe"].load(meta.path, meta.channel("note"), meta) == "hello"
    finally:
        from benchgate.lab.store import CHANNEL_KINDS

        CHANNEL_KINDS.pop("probe", None)


def test_thermal_hotspot_and_calibrate_without_hardware(tmp_path):
    from benchgate.agent.dispatch import dispatch
    from benchgate.lab.thermal import summarize_thermal

    store = LabDataStore(tmp_path / "models" / "captured")
    t0 = datetime.now(timezone.utc)
    values = np.zeros((8, 8), dtype=float)
    values[2, 3] = 50
    frame = Frame2D(
        values=values,
        unit="count",
        quantity=QuantityKind.TEMPERATURE,
        timestamp=t0,
        metadata={"idn": "fake", "emissivity": 1.0},
    )
    derived = summarize_thermal(frame, instrument_idn="fake")
    meta = store.write_session(frames={"thermal": frame}, derived=derived)
    (tmp_path / "models").mkdir(exist_ok=True)
    # dispatch resolves design paths; use a dummy design dir with captured store layout
    # by patching is messy — call the helpers via store + dispatch calibrate only.
    from benchgate.lab.thermal import affine_from_points

    cal = affine_from_points([(2800.0, 20.0), (3200.0, 40.0)], instrument_idn="fake")
    assert cal.kind == "affine2pt"
    assert cal.slope == pytest.approx(0.05)
    result = dispatch(
        "lab_thermal_calibrate",
        {
            "design_dir": str(tmp_path),
            "points": ["2800:20", "3200:40"],
            "instrument_idn": "fake-idn",
            "path": str(tmp_path / "cal.yaml"),
        },
    )
    assert result["kind"] == "affine2pt"
    assert (tmp_path / "cal.yaml").is_file()
    assert meta.derived["hotspot_row"] == 2.0
    assert meta.derived["hotspot_col"] == 3.0

    from benchgate.lab.thermal import fixture_id, fixture_id_hash_float

    fid = fixture_id(instrument_idn="fake", emissivity=1.0, warmup_s=30.0)
    frame_fid = Frame2D(
        values=values,
        unit="count",
        quantity=QuantityKind.TEMPERATURE,
        timestamp=t0,
        metadata={"idn": "fake", "emissivity": 1.0, "fixture_id": fid, "warmup_s": 30.0},
    )
    stored = store.write_session(frames={"thermal": frame_fid}, derived={"t_max": 50.0})
    hot = dispatch("lab_thermal_hotspot", {"design_dir": str(tmp_path), "session": stored.session_id})
    assert hot["fixture_id"] == fid
    assert hot["derived"]["fixture_id_hash"] == pytest.approx(fixture_id_hash_float(fid))
