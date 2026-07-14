"""Tests for HTOOL SA8 SCPI helpers and driver (no hardware)."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from benchgate.instruments import (
    CalStandard,
    CapabilityError,
    PeakMode,
    RFSource,
    ScanConfig,
    SpectrumAnalyzer,
    SparamKind,
    VectorAnalyzer,
    load_bench,
)
from benchgate.instruments.drivers.htool_sa8 import HtoolSA8
from benchgate.instruments.scpi import (
    decode_sa8_history_payload,
    decode_sa8_sweep_payload,
    parse_arbitrary_block,
    parse_labeled_value,
    read_arbitrary_block,
)
from benchgate.instruments.types import Spectrum


def _make_sweep_payload() -> bytes:
    header = b"\x00" * 6
    body = bytearray()
    for i in range(302):
        amp_raw = int(-3000 + i)  # -30.00 dBm .. -26.99 dBm
        freq_khz = 100_000 + i * 1000
        body.extend(struct.pack("<hi", amp_raw, freq_khz))
    return header + bytes(body) + b"\r\n"


def test_parse_labeled_value():
    assert parse_labeled_value("CENT:123.45", "CENT") == "123.45"
    assert parse_labeled_value("CENT：99.0", "CENT") == "99.0"


def test_parse_arbitrary_block():
    payload = b"\x01\x02\x03\x04"
    buf = b"#14" + payload + b"\r\n"
    out, end = parse_arbitrary_block(buf)
    assert out == payload
    assert end == len(buf)


def test_read_arbitrary_block():
    payload = _make_sweep_payload()
    block = f"#{len(str(len(payload)))}{len(payload)}".encode("ascii") + payload + b"\r\n"
    pos = {"i": 0}

    def read(n: int) -> bytes:
        chunk = block[pos["i"] : pos["i"] + n]
        pos["i"] += n
        return chunk

    assert read_arbitrary_block(read) == payload


def test_decode_sa8_sweep_payload():
    payload = _make_sweep_payload()
    freq_hz, amps_dbm = decode_sa8_sweep_payload(payload)
    assert len(freq_hz) == 302
    assert len(amps_dbm) == 302
    assert freq_hz[0] == pytest.approx(100e6)
    assert amps_dbm[0] == pytest.approx(-30.0)
    assert amps_dbm[-1] == pytest.approx(-26.99)


def test_decode_sa8_history_payload():
    raw = np.array([-1000, -900, -800], dtype="<i2").tobytes()
    freq_hz, amps_dbm = decode_sa8_history_payload(raw, start_hz=1e6, stop_hz=3e6)
    assert len(freq_hz) == 3
    assert amps_dbm[0] == pytest.approx(-10.0)
    assert freq_hz[-1] == pytest.approx(3e6)


class _FakeScpi:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.is_open = False
        self._sweep = _make_sweep_payload()
        self._history = np.array([-500, -400], dtype="<i2").tobytes()

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def write(self, cmd: str) -> None:
        self.writes.append(cmd.rstrip("\r\n"))

    def query(self, cmd: str) -> str:
        self.write(cmd)
        queries = {
            "*IDN?": "HTOOL,SA8,TEST,3.2.0",
            "FREQuency:CENTer?": "CENT:100.0",
            "FREQuency:SPAN?": "SPAN:50.0",
            "FREQuency:STARt?": "STAR:75.0",
            "FREQuency:STOP?": "STOP:125.0",
            "MEASure:PEAK?": '"Peak:-12.5,100.0"',
            "MEASure:FLOOr?": '"Floor:633.669"',
            "GENErator:STATus?": "STAT:ON",
            "GENE:FREQ?": "FREQ:433.92",
        }
        return queries[cmd.rstrip("\r\n")]

    def query_block(self, cmd: str) -> bytes:
        self.write(cmd)
        if cmd.startswith("DATA:CURRent"):
            return self._sweep
        if cmd.startswith("DATA:HISTory"):
            return self._history
        raise KeyError(cmd)

    def query_fixed(self, cmd: str, nbytes: int) -> bytes:
        data = self.query_block(cmd)
        if len(data) >= nbytes:
            return data[:nbytes]
        return data


def test_htool_sa8_capabilities_and_sweep():
    t = _FakeScpi()
    sa = HtoolSA8("sa0", "/dev/fake", transport=t)
    sa.connect()
    assert sa.identify() == "HTOOL,SA8,TEST,3.2.0"
    assert isinstance(sa, SpectrumAnalyzer)
    assert isinstance(sa, RFSource)
    assert isinstance(sa, VectorAnalyzer)

    sa.configure_scan(ScanConfig(center_mhz=100.0, span_mhz=50.0, reference_dbm=-20.0))
    assert "FREQuency:SCAN:CENTer 100.0,50.0" in t.writes
    assert "AMPLitude:REFEerence -20.0" in t.writes

    spec = sa.capture_spectrum()
    assert isinstance(spec, Spectrum)
    assert len(spec) == 302
    assert spec.freq_hz[1] == pytest.approx(101e6)

    peak = sa.measure_peak(PeakMode.AVR)
    assert peak.value == pytest.approx(-12.5)
    assert peak.raw and peak.raw.get("freq_mhz") == pytest.approx(100.0)
    floor = sa.measure_floor()
    assert floor.value == pytest.approx(633.669)
    assert floor.unit == "ADC"

    sa.set_generator_enabled(True)
    sa.set_generator_frequency_mhz(433.92)
    assert "GENErator:STATus ON" in t.writes
    assert sa.query_generator_enabled() is True

    sa.calibrate_sparam(SparamKind.S21, CalStandard.OPEN, enabled=True)
    assert "DATA:SPARam:CALIbrate S21,OPEN,ON" in t.writes
    sa.calibrate_sparam(SparamKind.S11, CalStandard.OPEN, enabled=False)
    assert "DATA:SPARam:CALIbrate S11,OFF" in t.writes

    trace = sa.capture_sparam_trace(SparamKind.S21)
    assert trace.trace == "s21"
    assert len(trace) == 2
    sa.disconnect()


def test_registry_sa_roles(tmp_path):
    cfg = tmp_path / "instruments.yaml"
    cfg.write_text(
        """
instruments:
  sa8:
    driver: htool_sa8
    transport: serial_scpi
    address: "/dev/fake"
roles:
  scope: null
  dmm: null
  awg: null
  sa: sa8
  rfgen: sa8
  vna: sa8
""",
        encoding="utf-8",
    )
    bench = load_bench(cfg)
    assert bench.instrument_for_role("sa") == "sa8"
    inst = bench.create("sa8")
    assert isinstance(inst, SpectrumAnalyzer)
    assert isinstance(inst, RFSource)
    assert isinstance(inst, VectorAnalyzer)


def test_scope_cannot_bind_sa_role(tmp_path):
    cfg = tmp_path / "instruments.yaml"
    cfg.write_text(
        """
instruments:
  scope_main:
    driver: rigol_ds1104z
    transport: visa
    address: "USB0::x::INSTR"
roles:
  scope: scope_main
  dmm: null
  awg: null
  sa: scope_main
  rfgen: null
  vna: null
""",
        encoding="utf-8",
    )
    bench = load_bench(cfg)
    with pytest.raises(CapabilityError):
        bench.open_instrument("scope_main", required_role="sa")
