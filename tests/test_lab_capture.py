"""Integration test: LabSession + capture_and_fit over a fake bench (no hardware)."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from benchgate.instruments import registry
from benchgate.instruments.base import Instrument
from benchgate.instruments.types import InstrumentInfo, QuantityKind, Reading, Waveform
from benchgate.lab.capture import LabConfig, LabSession, capture_and_fit
from benchgate.lab.store import LabDataStore


class FakeScope(Instrument):
    def __init__(self, name, address, **opts):
        super().__init__(name, address)

    @property
    def info(self):
        return InstrumentInfo("fake_scope", self._address, "visa", "FAKE-SCOPE")

    def connect(self):
        self._open = True

    def disconnect(self):
        self._open = False

    def auto_setup(self):
        pass

    def enable_channel(self, channel=1, enabled=True):
        pass

    def configure_trigger(self, config):
        self.trigger = config

    def single_capture(self):
        self.armed = True

    def capture_waveform(self, channel=1):
        t = np.linspace(0, 1e-3, 200)
        v = 3.3 * (1.0 - np.exp(-t / 2e-4))  # RC charge, tau = 2e-4
        return Waveform(t, v, channel, datetime.now(timezone.utc), sample_rate_hz=2e5)

    def screenshot_png(self):
        return b"PNG"


class FakeDmm(Instrument):
    def __init__(self, name, address, **opts):
        super().__init__(name, address)

    @property
    def info(self):
        return InstrumentInfo("fake_dmm", self._address, "serial", "FAKE-DMM")

    def connect(self):
        self._open = True

    def disconnect(self):
        self._open = False

    def read(self):
        return Reading(3.29, "V", QuantityKind.VOLTAGE, datetime.now(timezone.utc), 3.29, "V", {"dc": True})


class FakeAwg(Instrument):
    def __init__(self, name, address, **opts):
        super().__init__(name, address)
        self.levels = []
        self.edges = []

    @property
    def info(self):
        return InstrumentInfo("fake_awg", self._address, "serial", "FAKE-AWG")

    def connect(self):
        self._open = True

    def disconnect(self):
        self._open = False

    def set_level(self, channel, high):
        self.levels.append((channel, high))

    def step_edge(self, channel, *, rising=True):
        self.edges.append((channel, rising))


@pytest.fixture
def fake_drivers(monkeypatch):
    monkeypatch.setitem(registry.DRIVER_REGISTRY, "fake_scope", FakeScope)
    monkeypatch.setitem(registry.DRIVER_REGISTRY, "fake_dmm", FakeDmm)
    monkeypatch.setitem(registry.DRIVER_REGISTRY, "fake_awg", FakeAwg)
    yield


def _bench_yaml(path, *, awg=True):
    awg_line = "  awg: awg0" if awg else "  awg: null"
    awg_inst = (
        "  awg0:\n    driver: fake_awg\n    transport: serial\n    address: fake-awg\n" if awg else ""
    )
    path.write_text(
        f"""
instruments:
  scope0:
    driver: fake_scope
    transport: visa
    address: fake-scope
  dmm0:
    driver: fake_dmm
    transport: serial
    address: fake-dmm
{awg_inst}roles:
  scope: scope0
  dmm: dmm0
{awg_line}
""",
        encoding="utf-8",
    )


def _fast_config():
    return LabConfig(dmm_readings=3, dmm_settle_s=0.0, edge_settle_s=0.0, scope_channel=1)


def test_capture_and_fit_with_awg(tmp_path, fake_drivers):
    cfg = tmp_path / "instruments.yaml"
    _bench_yaml(cfg, awg=True)
    bench = registry.load_bench(cfg)
    store = LabDataStore(tmp_path / "captured")

    with LabSession(bench, _fast_config()) as session:
        measured, meta = capture_and_fit(
            session,
            store,
            component_ref="C1",
            mpn="100n",
            kicad_key="Device:C::100n",
            design="design/myboard",
            tags=["rc_step"],
        )
        # AWG produced an idle level then a rising edge.
        assert session._awg.levels  # idle set
        assert session._awg.edges == [("pg13", True)]

    # Fit recovered tau ~ 2e-4 and the DMM steady value.
    assert measured.params["tau_s"] == pytest.approx(2e-4, rel=0.1)
    assert measured.params["dmm_steady"] == pytest.approx(3.29)
    assert measured.params["dmm_readings_n"] == 3
    assert measured.session_id == meta.session_id

    # Session persisted with both channels + derived metrics.
    reloaded = store.get_session(meta.session_id)
    assert reloaded.component_ref == "C1"
    assert reloaded.channel("scope_ch1").kind == "waveform"
    assert reloaded.channel("dmm").kind == "scalar_series"
    assert "dmm_steady" in reloaded.derived
    assert reloaded.roles["awg"] == "awg0"
    assert measured.session_id == meta.session_id


def test_capture_without_awg(tmp_path, fake_drivers):
    cfg = tmp_path / "instruments.yaml"
    _bench_yaml(cfg, awg=False)
    bench = registry.load_bench(cfg)
    store = LabDataStore(tmp_path / "captured")

    with LabSession(bench, _fast_config()) as session:
        assert session._awg is None  # unbound -> manual/external stimulus path
        wf = session.capture_step_response()
        assert len(wf) == 200

    assert bench.instrument_for_role("awg") is None


def test_capture_defaults_from_project_lab_yaml(tmp_path, fake_drivers):
    cfg = tmp_path / "instruments.yaml"
    _bench_yaml(cfg, awg=False)
    project = tmp_path / "lab.yaml"
    project.write_text("capture:\n  dmm_readings: 7\n  scope_channel: 3\n", encoding="utf-8")
    bench = registry.load_bench(cfg, project_lab_path=project)

    # LabConfig derived from the bench's capture section picks up project defaults.
    session = LabSession(bench)
    assert session.config.dmm_readings == 7
    assert session.config.scope_channel == 3
