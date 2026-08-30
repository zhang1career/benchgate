"""Umeko DEC-H driver tests against a fake CDC shell."""

from __future__ import annotations

import numpy as np
import pytest

from benchgate.instruments.drivers.umeko_dec_h import FRAME_H, FRAME_W, UmekoDecH, _parse_emissivity
from benchgate.instruments.errors import DecodeError, InstrumentError, TimeoutInstrumentError
from benchgate.instruments.transport import SerialShellTransport
from benchgate.instruments.types import RetryPolicy


def _peak_frame(row: int = 5, col: int = 7, peak: int = 4000, floor: int = 2900) -> bytes:
    grid = np.full((FRAME_H, FRAME_W), floor, dtype="<u2")
    grid[row, col] = peak
    grid[1, 31] = 65000  # listed bad pixel (31, 1) -> (row=1, col=31)
    return grid.tobytes()


class _FakePicoSerial:
    def __init__(
        self,
        frame: bytes,
        *,
        extra_after_payload: bytes = b"",
        header: bytes = b"\xa0\x0f\x54\x0b",  # t_max=4000, t_min=2900
        vary_frames: bool = True,
    ):
        self.is_open = False
        self.buf = bytearray()
        self.writes: list[str] = []
        self.frame = frame
        self.extra_after_payload = extra_after_payload
        self.header = header
        self.vary_frames = vary_frames
        self._n_stream = 0

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def set_dtr(self, _v):
        pass

    def set_rts(self, _v):
        pass

    def flush_input(self):
        self.buf.clear()

    def write(self, data):
        text = data if isinstance(data, str) else data.decode()
        self.writes.append(text)
        cmd = text.strip()
        if cmd == "query":
            self.buf += b"emissivity: 100\r\n"
        elif cmd == "help":
            self.buf += b"=== Command Help ===\r\n  getmat\r\n  stream\r\n"
        elif cmd.startswith("badpix -q"):
            self.buf += b""
        elif cmd.startswith("badpix"):
            self.buf += b"Bad pixels (3/10):\n  1: (31, 1)\n  2: (25, 24)\n  3: (25, 23)\n"
        elif cmd == "stop_stream":
            self.buf += b"streaming stopped\r\n"
        elif cmd == "stream":
            grid = np.frombuffer(self.frame, dtype="<u2").reshape(FRAME_H, FRAME_W).copy()
            if self.vary_frames:
                grid[0, 0] = np.uint16(2800 + self._n_stream)
            self._n_stream += 1
            rec = b"streaming started\r\nBEGIN" + self.header + grid.tobytes()
            rec += self.extra_after_payload + b"END"
            self.buf += rec
        elif cmd.startswith("set -e"):
            self.buf += b"ok\r\n"

    def read(self, n):
        chunk = bytes(self.buf[:n])
        del self.buf[:n]
        return chunk

    def read_until(self, expected, max_bytes=4096):
        data = bytes(self.buf)
        idx = data.find(expected)
        if idx < 0:
            out = data[:max_bytes]
            del self.buf[: len(out)]
            return out
        end = idx + len(expected)
        out = data[:end]
        del self.buf[:end]
        return out

    def read_until_text(self, marker, *, deadline_s):
        return self.read_until(marker.encode("ascii")).decode("ascii", errors="replace")


def _cam(frame: bytes | None = None, **kwargs) -> UmekoDecH:
    raw = frame if frame is not None else _peak_frame()
    inner = _FakePicoSerial(raw, **kwargs)
    shell = SerialShellTransport("/dev/fake", timeout_s=1.0, assert_dtr=True, inner=inner)
    return UmekoDecH(
        "pico",
        "/dev/fake",
        transport=shell,
        settle_s=0.0,
        retry=RetryPolicy(attempts=1, backoff_s=0),
    )


def test_parse_emissivity_is_percent():
    assert _parse_emissivity("emissivity: 100") == pytest.approx(1.0)
    assert _parse_emissivity("emissivity: 1") == pytest.approx(0.01)
    with pytest.raises(Exception, match="emissivity"):
        _parse_emissivity("nope")


def test_connect_recovers_from_badpix_submode_and_reads_mask():
    cam = _cam()
    cam.connect()
    assert "umeko-dec-h:2e8a:000a:" in cam.identify()
    assert "fw" in cam.identify()
    assert cam.frame_shape == (32, 32)
    assert (1, 31) in cam.bad_pixels()
    assert cam.get_emissivity() == pytest.approx(1.0)
    cam.disconnect()


def test_capture_frame_uses_begin_end_and_masks_bad_pixel():
    cam = _cam()
    cam.connect()
    frame = cam.capture_frame()
    assert frame.unit == "count"
    assert frame.calibration is None
    assert frame.values.shape == (32, 32)
    assert frame.values[5, 7] == 4000
    assert frame.mask is not None and bool(frame.mask[1, 31])
    from benchgate.lab.field2d import find_max

    spot = find_max(frame)
    assert (spot.row, spot.col) == (5, 7)
    assert frame.metadata["device_t_max_raw"] == 4000
    assert frame.metadata["device_t_min_raw"] == 2900
    assert frame.metadata["frames_unique"] == 1
    assert frame.metadata["frames_received"] == 1
    cam.disconnect()


def test_misaligned_frame_raises():
    cam = _cam(extra_after_payload=b"\x00")
    cam.connect()
    with pytest.raises((DecodeError, Exception)):
        cam.capture_frame()
    cam.disconnect()


def test_refuses_bootloader():
    cam = _cam()
    cam.connect()
    with pytest.raises(InstrumentError, match="refusing"):
        cam._send("bootloader")
    cam.disconnect()


def test_burst_two_unique_frames():
    cam = _cam()
    cam.connect()
    series = cam.capture_burst(2)
    assert len(series) == 2
    assert series.unit == "count"
    assert series.values[0, 0, 0] != series.values[1, 0, 0]
    assert series.metadata["frames_unique"] == 2
    assert series.metadata["frames_received"] == 2
    assert series.metadata["device_t_max_raw"] == 4000
    cam.disconnect()


def test_burst_times_out_when_payload_never_changes():
    cam = _cam(vary_frames=False)
    cam.connect()
    with pytest.raises(TimeoutInstrumentError, match="unique frames"):
        cam.capture_burst(2, timeout_s=0.6)
    cam.disconnect()
