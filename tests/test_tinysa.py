"""Tests for tinySA console helpers and driver (no hardware)."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from benchgate.instruments import (
    CapabilityError,
    PeakMode,
    RFSource,
    ScanConfig,
    SpectrumAnalyzer,
    VectorAnalyzer,
    load_bench,
)
from benchgate.instruments.drivers.tinysa import (
    TinySA,
    decode_scanraw_payload,
    parse_marker_peak_line,
    parse_sweep_status,
    resolve_tinysa_address,
)
from benchgate.instruments.types import Spectrum


def _scanraw_frame(points: int, *, seed: int = 1000) -> bytes:
    body = bytearray()
    for i in range(points):
        raw = seed + i
        body.extend(b"x")
        body.extend(struct.pack("<H", raw))
    return b"scanraw 1 2 " + str(points).encode() + b"\r\n{" + bytes(body) + b"}ch> "


def test_decode_scanraw_payload():
    points = 4
    frame = _scanraw_frame(points)
    body = frame[frame.find(b"{") + 1 : frame.rfind(b"}")]
    amps = decode_scanraw_payload(body, points=points, dbm_offset=128.0)
    assert len(amps) == 4
    assert amps[0] == pytest.approx(1000 / 32 - 128)
    assert amps[-1] == pytest.approx(1003 / 32 - 128)


def test_parse_marker_and_sweep():
    dbm, freq = parse_marker_peak_line("marker 1 peak\r\n1 0 1000000 -3.04e+01\r\nch>")
    assert dbm == pytest.approx(-30.4)
    assert freq == pytest.approx(1e6)
    start, stop, pts = parse_sweep_status("sweep\r\n1000000 30000000 290\r\nch>")
    assert start == pytest.approx(1e6)
    assert stop == pytest.approx(30e6)
    assert pts == 290


def test_resolve_path_passthrough():
    assert resolve_tinysa_address("/dev/cu.usbmodem4001") == "/dev/cu.usbmodem4001"


class _FakeSerial:
    def __init__(self) -> None:
        self.is_open = False
        self.writes: list[str] = []
        self._queue: list[bytes] = []
        self._points = 8

    def open(self) -> None:
        self.is_open = True
        self._queue.append(b"ch>")

    def close(self) -> None:
        self.is_open = False

    def flush_input(self) -> None:
        self._queue.clear()

    def write(self, data: bytes | str) -> None:
        if isinstance(data, bytes):
            text = data.decode("ascii", errors="replace")
        else:
            text = data
        self.writes.append(text.rstrip("\r\n"))
        cmd = text.strip().rstrip("\r")
        if cmd == "" or cmd == "\r":
            self._queue.append(b"ch>")
            return
        if cmd == "info":
            self._queue.append(
                b"info\r\ntinySA v0.3\r\nVersion: tinySA_v1.4-175\r\nch>"
            )
        elif cmd == "mode low input":
            self._queue.append(b"mode low input\r\nch>")
        elif cmd.startswith("sweep center") or cmd.startswith("sweep span"):
            self._queue.append((cmd + "\r\nch>").encode())
        elif cmd.startswith("sweep start") or cmd.startswith("sweep stop"):
            self._queue.append((cmd + "\r\nch>").encode())
        elif cmd == "sweep":
            self._queue.append(b"sweep\r\n1000000 30000000 290\r\nch>")
        elif cmd.startswith("attenuate") or cmd.startswith("trace "):
            self._queue.append((cmd + "\r\nch>").encode())
        elif cmd == "pause" or cmd == "resume":
            self._queue.append((cmd + "\r\nch>").encode())
        elif cmd.startswith("scanraw"):
            self._queue.append(_scanraw_frame(self._points))
        elif cmd == "marker 1 peak":
            self._queue.append(b"marker 1 peak\r\n1 0 1000000 -3.04e+01\r\nch>")
        elif cmd.startswith("mode ") or cmd.startswith("output ") or cmd.startswith("level ") or cmd.startswith("freq "):
            self._queue.append((cmd + "\r\nch>").encode())
        else:
            self._queue.append((cmd + "\r\nch>").encode())

    def read(self, size: int) -> bytes:
        if not self._queue:
            return b""
        data = self._queue.pop(0)
        if len(data) <= size:
            return data
        self._queue.insert(0, data[size:])
        return data[:size]

    def read_until_text(self, marker: str, *, deadline_s: float) -> str:
        if not self._queue:
            return marker
        return self._queue.pop(0).decode("ascii", errors="replace")


def test_tinysa_capabilities_and_sweep():
    t = _FakeSerial()
    sa = TinySA("sa0", "/dev/fake", points=8, transport=t)
    sa.connect()
    assert "tinySA v0.3" in sa.identify()
    assert isinstance(sa, SpectrumAnalyzer)
    assert isinstance(sa, RFSource)
    assert not isinstance(sa, VectorAnalyzer)

    sa.configure_scan(ScanConfig(start_mhz=1.0, stop_mhz=30.0, attenuation=10))
    assert any(w.startswith("sweep start") for w in t.writes)
    assert "attenuate 10" in t.writes

    spec = sa.capture_spectrum()
    assert isinstance(spec, Spectrum)
    assert len(spec) == 8
    assert spec.freq_hz[0] == pytest.approx(1e6)
    assert spec.amplitude_dbm[0] == pytest.approx(1000 / 32 - 128)

    peak = sa.measure_peak(PeakMode.AVR)
    assert peak.value == pytest.approx(-30.4)
    assert peak.raw and peak.raw["freq_mhz"] == pytest.approx(1.0)

    sa.set_generator_frequency_mhz(10.0)
    sa.set_generator_power_dbm(-30)
    sa.set_generator_enabled(True)
    assert "mode low output" in t.writes
    assert "output on" in t.writes
    assert sa.query_generator_enabled() is True
    assert sa.query_generator_frequency_mhz() == pytest.approx(10.0)
    sa.disconnect()


def test_registry_tinysa_sa_role(tmp_path):
    cfg = tmp_path / "instruments.yaml"
    cfg.write_text(
        """
instruments:
  tinysa:
    driver: tinysa
    transport: serial
    address: "/dev/fake"
roles:
  scope: null
  dmm: null
  awg: null
  sa: tinysa
  rfgen: tinysa
  vna: null
""",
        encoding="utf-8",
    )
    bench = load_bench(cfg)
    assert bench.instrument_for_role("sa") == "tinysa"
    inst = bench.create("tinysa")
    assert isinstance(inst, SpectrumAnalyzer)
    assert isinstance(inst, RFSource)
    with pytest.raises(CapabilityError):
        bench.open_instrument("tinysa", required_role="vna")
