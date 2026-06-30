"""Tests for the unified instrument layer (no hardware; transports are mocked)."""

from __future__ import annotations

import numpy as np
import pytest

from benchgate.instruments import (
    Bench,
    CapabilityError,
    ConfigError,
    Oscilloscope,
    RetryPolicy,
    ScalarReader,
    TriggerConfig,
    load_bench,
)
from benchgate.instruments.drivers.rigol_ds1104 import DS1104Scope
from benchgate.instruments.drivers.tars_shell import TarsStimulus
from benchgate.instruments.drivers.uni_t_ut61e import EOL, RAW_LEN, UT61EDecoder, UT61EDmm

# --- A synthetic but structurally valid ES51922 DC-volts frame ---
# range_id=2 -> RANGE_V[2]=('220.00','V',0.01); digits 1,2,3,4,5 -> 12345*0.01 = 123.45 V; DC set.
DCV_FRAME = [0x32, 0x31, 0x32, 0x33, 0x34, 0x35, 0x3B, 0x30, 0x30, 0x30, 0x38, 0x30, 0x0D, 0x0A]


def test_decoder_dc_voltage():
    res = UT61EDecoder().decode(DCV_FRAME)
    assert res["data_valid"] is True
    assert res["mode"] == "V/mV"
    assert res["units"] == "V"
    assert res["val"] == pytest.approx(123.45)
    assert res["dc"] is True
    assert res["ac"] is False
    assert res["norm_val"] == pytest.approx(123.45)


def test_decoder_low_bat_bit_fixed():
    # byte7 = 0x32 sets LOW_BAT (0x02) but NOT PERCENT (0x08); the original bug
    # read low_bat from the PERCENT mask, which would yield False here.
    frame = list(DCV_FRAME)
    frame[7] = 0x32
    res = UT61EDecoder().decode(frame)
    assert res["low_bat"] is True
    assert res["percent"] is False


def test_decoder_rejects_wrong_length():
    with pytest.raises(Exception):
        UT61EDecoder().decode([0x30] * 10)


class _FakeSerial:
    """Minimal SerialTransport stand-in for the UT61E driver."""

    def __init__(self, frame: bytes):
        self._frame = frame
        self.is_open = False

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def flush_input(self):
        pass

    def read_until(self, expected, *, max_bytes=4096):
        return self._frame


def test_ut61e_read_returns_reading():
    frame = bytes(DCV_FRAME)
    dmm = UT61EDmm("dmm0", "/dev/fake", transport=_FakeSerial(frame))
    dmm.connect()
    reading = dmm.read()
    assert reading.value == pytest.approx(123.45)
    assert reading.unit == "V"
    assert reading.quantity.value == "voltage"
    assert reading.flags["dc"] is True
    dmm.disconnect()


def test_ut61e_retries_then_raises_on_garbage():
    short = bytes([0x30, 0x30, 0x0D, 0x0A])  # never 14 bytes
    dmm = UT61EDmm("dmm0", "/dev/fake", transport=_FakeSerial(short), retry=RetryPolicy(attempts=3, backoff_s=0))
    dmm.connect()
    with pytest.raises(Exception):
        dmm.read()


class _FakeVisa:
    """VisaTransport stand-in for the scope driver."""

    QUERIES = {
        "*IDN?": "RIGOL,DS1104Z,TEST,1.0",
        ":WAV:XINC?": "1e-6",
        ":WAV:XOR?": "0",
        ":WAV:YINC?": "0.01",
        ":WAV:YOR?": "0",
        ":WAV:YREF?": "127",
    }

    def __init__(self):
        self.writes = []

    def open(self):
        pass

    def close(self):
        pass

    def write(self, cmd):
        self.writes.append(cmd)

    def query(self, cmd):
        return self.QUERIES[cmd]

    def query_binary_values(self, cmd, **kwargs):
        return np.array([127, 137, 117], dtype=float)

    def read_raw(self):
        return b"\x89PNG"


def test_ds1104_capture_waveform_scaling():
    t = _FakeVisa()
    scope = DS1104Scope("scope0", "USB::x", transport=t)
    scope.connect()
    assert scope.identify() == "RIGOL,DS1104Z,TEST,1.0"
    scope.configure_trigger(TriggerConfig(source_channel=1, level_v=1.0))
    assert ":TRIG:EDGE:SLOP POS" in t.writes
    wf = scope.capture_waveform(1)
    # (data - 127) * 0.01: [0.0, 0.1, -0.1]
    assert wf.voltage_v[0] == pytest.approx(0.0)
    assert wf.voltage_v[1] == pytest.approx(0.1)
    assert wf.voltage_v[2] == pytest.approx(-0.1)
    assert wf.time_s[1] == pytest.approx(1e-6)
    assert isinstance(scope, Oscilloscope)


class _FakeTarsSerial:
    def __init__(self):
        self.is_open = False
        self.last = ""
        self.writes = []

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def set_dtr(self, v):
        pass

    def set_rts(self, v):
        pass

    def flush_input(self):
        pass

    def write(self, data):
        self.last = data if isinstance(data, str) else data.decode()
        self.writes.append(self.last)

    def read_until_text(self, marker, *, deadline_s):
        cmd = self.last
        if "gpio write" in cmd:
            parts = cmd.split()
            return f"mcu gpio write: pin={parts[3]} val={parts[4]}\r\ntars> "
        if "gpio read" in cmd:
            return "mcu gpio read: pin=pg13 val=1\r\ntars> "
        if "mcu info" in cmd:
            return "mcu: stm32f429i-disc1\r\ntars> "
        return "TARS shell ready.\r\ntars> "


def test_tars_set_level_and_read():
    t = _FakeTarsSerial()
    tars = TarsStimulus("tars0", "/dev/usbmodem", transport=t, ready_timeout_s=0.1)
    tars.connect()
    tars.set_level("pg13", high=True)
    assert any("mcu gpio write pg13 1" in w for w in t.writes)
    tars.step_edge("pg13", rising=True)
    assert tars.read_level("pg13") == 1
    tars.disconnect()


def _write_instruments_yaml(path):
    path.write_text(
        """
defaults:
  visa_backend: null
instruments:
  scope_main:
    driver: rigol_ds1104z
    transport: visa
    address: "USB0::x::INSTR"
  dmm_bench:
    driver: uni_t_ut61e
    transport: serial
    address: "/dev/cu.fake"
roles:
  scope: scope_main
  dmm: dmm_bench
  awg: null
""",
        encoding="utf-8",
    )


def test_load_bench_and_role_overrides(tmp_path):
    cfg = tmp_path / "instruments.yaml"
    _write_instruments_yaml(cfg)
    bench = load_bench(cfg)
    assert isinstance(bench, Bench)
    assert bench.instrument_for_role("scope") == "scope_main"
    assert bench.instrument_for_role("dmm") == "dmm_bench"
    assert bench.instrument_for_role("awg") is None

    bench2 = load_bench(cfg, overrides={"scope": "scope_main", "dmm": None})
    assert bench2.instrument_for_role("dmm") is None


def test_capability_mismatch_raises(tmp_path):
    cfg = tmp_path / "instruments.yaml"
    _write_instruments_yaml(cfg)
    bench = load_bench(cfg)
    # Asking the scope to play the dmm role must fail before any I/O.
    with pytest.raises(CapabilityError):
        bench.open_instrument("scope_main", required_role="dmm")


def test_unknown_instrument_raises(tmp_path):
    cfg = tmp_path / "instruments.yaml"
    _write_instruments_yaml(cfg)
    bench = load_bench(cfg)
    with pytest.raises(ConfigError):
        bench.create("nope")


def test_project_lab_yaml_three_layer(tmp_path):
    cfg = tmp_path / "instruments.yaml"
    _write_instruments_yaml(cfg)  # roles: scope_main, dmm_bench, awg null
    project = tmp_path / "lab.yaml"
    project.write_text(
        """
instruments:
  tars_local:
    driver: tars_shell
    transport: serial
    address: "/dev/cu.usbmodemLOCAL"
roles:
  awg: tars_local
capture:
  dmm_readings: 9
  scope_channel: 2
""",
        encoding="utf-8",
    )

    # Layer 2 (project) adds a local instrument + rebinds awg + capture defaults.
    bench = load_bench(cfg, project_lab_path=project)
    assert "tars_local" in bench.instruments
    assert bench.instrument_for_role("awg") == "tars_local"
    assert bench.instrument_for_role("scope") == "scope_main"  # inherited from global
    assert bench.capture["dmm_readings"] == 9
    assert bench.capture["scope_channel"] == 2

    # Layer 3 (runtime overrides) beats the project binding.
    bench2 = load_bench(cfg, project_lab_path=project, overrides={"awg": None})
    assert bench2.instrument_for_role("awg") is None


def test_env_override_by_role_and_name(tmp_path):
    cfg = tmp_path / "instruments.yaml"
    _write_instruments_yaml(cfg)

    # Role-based override changes the address of the instrument bound to scope.
    bench = load_bench(cfg, env={"BENCHGATE_SCOPE_ADDRESS": "TCPIP::1.2.3.4::INSTR"})
    assert bench.instruments["scope_main"].address == "TCPIP::1.2.3.4::INSTR"

    # Per-instrument override is more specific and wins over role-based.
    bench2 = load_bench(
        cfg,
        env={
            "BENCHGATE_SCOPE_ADDRESS": "TCPIP::role::INSTR",
            "BENCHGATE_INSTRUMENT_SCOPE_MAIN_ADDRESS": "USB::byname::INSTR",
        },
    )
    assert bench2.instruments["scope_main"].address == "USB::byname::INSTR"

    # Serial port override for the DMM by name.
    bench3 = load_bench(cfg, env={"BENCHGATE_INSTRUMENT_DMM_BENCH_ADDRESS": "/dev/ttyUSB9"})
    assert bench3.instruments["dmm_bench"].address == "/dev/ttyUSB9"
