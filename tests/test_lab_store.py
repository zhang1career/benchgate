"""Tests for the S0 session store (write / query / load round-trips)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from benchgate.instruments.types import QuantityKind, ScalarSeries, Waveform
from benchgate.lab.store import LabDataStore, new_session_id


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
