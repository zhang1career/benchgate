"""Umeko DEC-H (RP2040 + Heimann HTPA32x32) thermal imager over USB CDC.

The device exposes a text shell (115200 8N1, DTR asserted). Capture uses the
``stream`` path. Official host framing (umeko-ir / umeko-py-thermal) is::

    BEGIN + t_max_raw(u16 LE) + t_min_raw(u16 LE) + 32x32 u16 LE + END

The two header uint16s are the device's display-range extrema, not
``min``/``max`` of the raw grid. Measured envelope (do not assert equality)::

    min(pixels) ≤ t_min_raw ≤ t_max_raw ≤ max(pixels)

Author SDK treats the uint16s as deci-Kelvin (``°C = raw * 0.1 - 273.15``).
This driver still stores the grid as ``unit="count"`` so gate cannot silently
compare them to a ``degC`` spec.

A single ``stream`` without heartbeat yields 11 frames then stops (~4 s
timeout); each unique payload is sent twice. Official SDK re-sends ``stream``
every 0.5 s. This driver keeps that heartbeat and returns **unique** frames.

Destructive shell commands are refused. ``badpix`` / ``cal`` enter a sub-mode
that swallows later commands; :meth:`connect` always exits that mode first.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone

import numpy as np

from ..base import Instrument, run_with_retry
from ..errors import DecodeError, InstrumentError, TimeoutInstrumentError, TransientInstrumentError
from ..transport import SerialShellTransport, SerialTransport
from ..types import Frame2D, Frame2DSeries, InstrumentInfo, QuantityKind, RetryPolicy

DRIVER_NAME = "umeko_dec_h"
FRAME_H = 32
FRAME_W = 32
FRAME_PIXELS = FRAME_H * FRAME_W
FRAME_BYTES = FRAME_PIXELS * 2
# BEGIN(5) + t_max(2) + t_min(2) + pixels(2048) + END(3) = 2060
FRAME_HEADER = 4
STREAM_KEEPALIVE_S = 0.5
# Unique payloads arrive at ~5.2/s with keepalive (each unique is sent twice).
_UNIQUE_FPS = 5.2
VID_PID = "2e8a:000a"

_FORBIDDEN_HEADS = frozenset(
    {
        "bootloader",
        "rm",
        "clear_photos",
        "save",
        "_cal_mat",
        "cat",
    }
)

_BADPIX_RE = re.compile(r"\((\d+)\s*,\s*(\d+)\)")
_EMISSIVITY_RE = re.compile(r"emissivity\s*:\s*([0-9.]+)", re.IGNORECASE)


class UmekoDecH(Instrument):
    def __init__(
        self,
        name: str,
        address: str,
        *,
        baud: int = 115200,
        timeout_s: float = 3.0,
        assert_dtr: bool = True,
        settle_s: float = 1.0,
        retry: RetryPolicy | None = None,
        transport: SerialShellTransport | SerialTransport | None = None,
    ) -> None:
        super().__init__(name, address, retry=retry)
        self.assert_dtr = assert_dtr
        self.settle_s = settle_s
        self.timeout_s = timeout_s
        if transport is not None and isinstance(transport, SerialShellTransport):
            self._t = transport
        elif transport is not None:
            self._t = SerialShellTransport(
                address, baud=baud, timeout_s=timeout_s, assert_dtr=assert_dtr, inner=transport
            )
        else:
            self._t = SerialShellTransport(
                address, baud=baud, timeout_s=timeout_s, assert_dtr=assert_dtr
            )
        self._idn = ""
        self._emissivity = 1.0
        self._bad: list[tuple[int, int]] = []
        self._mask: np.ndarray | None = None

    @property
    def frame_shape(self) -> tuple[int, int]:
        return (FRAME_H, FRAME_W)

    @property
    def info(self) -> InstrumentInfo:
        return InstrumentInfo(
            driver=DRIVER_NAME,
            address=self._address,
            transport="serial_shell",
            idn=self._idn or "umeko-dec-h",
            metadata={"sensor": "HTPA32x32", "unit": "count"},
        )

    def connect(self) -> None:
        if not self._t.is_open:
            self._t.open()
        if self.assert_dtr:
            try:
                self._t.set_dtr(True)
            except Exception:
                pass
        # CDC DTR often resets the RP2040; wait for the shell to come back.
        if self.settle_s > 0:
            time.sleep(self.settle_s)
        self._recover()
        alive = self._send("query")
        if "emissivity" not in alive.lower():
            raise TransientInstrumentError(f"DEC-H not at top-level shell: {alive!r}")
        self._emissivity = _parse_emissivity(alive)
        help_text = self._send("help")
        fw = hashlib.sha256(help_text.encode("utf-8")).hexdigest()[:8]
        usb_sn = _usb_serial(self._address) or "unknown"
        self._idn = f"umeko-dec-h:{VID_PID}:{usb_sn}:fw{fw}"
        self._refresh_bad_pixels()

    def disconnect(self) -> None:
        try:
            if self._t.is_open:
                self._t.write("stop_stream\r\n")
                self._t.drain(0.05)
        except Exception:
            pass
        self._t.close()

    def _recover(self) -> None:
        self._t.drain(0.05)
        self._t.write("badpix -q\r\n")
        self._t.drain(0.05)
        self._t.write("stop_stream\r\n")
        self._t.drain(0.08)

    def _send(self, cmd: str, *, quiet_s: float = 0.12) -> str:
        head = cmd.strip().split()[0] if cmd.strip() else ""
        if head in _FORBIDDEN_HEADS and not cmd.strip().startswith("badpix"):
            raise InstrumentError(f"refusing destructive or untrusted command {cmd!r}")
        if head == "_cali" and "-show" not in cmd:
            raise InstrumentError(f"refusing destructive command {cmd!r}")
        return self._t.command(cmd, quiet_s=quiet_s)

    def _refresh_bad_pixels(self) -> None:
        listing = self._send("badpix -l")
        self._send("badpix -q")
        self._bad = _parse_bad_pixels(listing)
        self._mask = _mask_from_bad(self._bad)

    def get_emissivity(self) -> float:
        text = self._send("query")
        self._emissivity = _parse_emissivity(text)
        return self._emissivity

    def set_emissivity(self, value: float) -> None:
        if not 0.01 <= float(value) <= 1.0:
            raise InstrumentError(f"emissivity out of range: {value}")
        self._send(f"set -e {int(round(float(value) * 100))}")
        self._emissivity = float(value)

    def bad_pixels(self) -> list[tuple[int, int]]:
        return list(self._bad)

    def capture_frame(self) -> Frame2D:
        def _do() -> Frame2D:
            self._recover()
            records, _t_rel, received = self._capture_stream_frames(1)
            image, header = records[0]
            return self._to_frame(image, header, frames_unique=1, frames_received=received)

        return run_with_retry(self.retry, _do, op="thermal.capture_frame")

    def capture_burst(self, count: int, *, timeout_s: float | None = None) -> Frame2DSeries:
        n = max(1, int(count))

        def _do() -> Frame2DSeries:
            self._recover()
            records, t_rel, received = self._capture_stream_frames(n, timeout_s=timeout_s)
            frames = [self._decode_counts(image) for image, _header in records]
            stack = np.stack(frames, axis=0)
            now = datetime.now(timezone.utc)
            first_header = records[0][1] if records else b""
            return Frame2DSeries(
                t_rel_s=np.asarray(t_rel, dtype=float),
                values=stack,
                unit="count",
                quantity=QuantityKind.TEMPERATURE,
                t0_utc=now,
                mask=None if self._mask is None else self._mask.copy(),
                calibration=None,
                metadata=self._frame_metadata(
                    first_header, frames_unique=len(records), frames_received=received
                ),
            )

        return run_with_retry(self.retry, _do, op="thermal.capture_burst")

    def _capture_stream_frames(
        self, count: int, *, timeout_s: float | None = None
    ) -> tuple[list[tuple[bytes, bytes]], list[float], int]:
        n = max(1, int(count))
        if timeout_s is not None:
            total_s = float(timeout_s)
            per = min(4.0, max(0.5, total_s))
        else:
            total_s = n / _UNIQUE_FPS * 3.0 + 4.0
            per = 4.0
        self._t.flush_input()
        self._t.write("stream\r\n")
        t0 = time.monotonic()
        deadline = t0 + total_s
        frames: list[tuple[bytes, bytes]] = []
        t_rel: list[float] = []
        seen: set[bytes] = set()
        received = 0
        try:
            while len(frames) < n:
                now = time.monotonic()
                if now >= deadline:
                    raise TimeoutInstrumentError(
                        f"thermal unique frames {len(frames)}/{n} after {received} received"
                    )
                remain = deadline - now
                begin_wait = min(STREAM_KEEPALIVE_S, max(0.05, remain))
                try:
                    image, header = self._read_one_stream_frame(
                        timeout_s=min(per, max(0.5, remain)),
                        begin_timeout_s=begin_wait,
                    )
                except TimeoutInstrumentError:
                    self._t.write("stream\r\n")
                    continue
                received += 1
                digest = hashlib.sha256(image).digest()
                if digest in seen:
                    continue
                seen.add(digest)
                frames.append((image, header))
                t_rel.append(time.monotonic() - t0)
        finally:
            self._t.write("stop_stream\r\n")
            self._t.drain(0.08)
        return frames, t_rel, received

    def _read_one_stream_frame(
        self, *, timeout_s: float, begin_timeout_s: float | None = None
    ) -> tuple[bytes, bytes]:
        self._t.read_until_marker(b"BEGIN", timeout_s=begin_timeout_s or timeout_s)
        header = self._t.read_exactly(FRAME_HEADER, timeout_s=1.0)
        image = self._t.read_exactly(FRAME_BYTES, timeout_s=timeout_s)
        if len(header) != FRAME_HEADER:
            raise DecodeError(f"thermal stream header {len(header)} != {FRAME_HEADER}")
        if len(image) != FRAME_BYTES:
            raise DecodeError(f"thermal frame size {len(image)} != {FRAME_BYTES}")
        self._expect_end()
        return image, header

    def _expect_end(self) -> None:
        marker = self._t.read_exactly(3, timeout_s=1.0)
        if marker != b"END":
            raise DecodeError(f"expected END after frame, got {marker!r}")

    def _decode_counts(self, raw: bytes) -> np.ndarray:
        if len(raw) != FRAME_BYTES:
            raise DecodeError(f"thermal frame size {len(raw)} != {FRAME_BYTES}")
        return np.frombuffer(raw, dtype="<u2").astype(np.float64).reshape(FRAME_H, FRAME_W)

    def _frame_metadata(
        self,
        header: bytes,
        *,
        frames_unique: int | None = None,
        frames_received: int | None = None,
    ) -> dict:
        meta: dict = {"idn": self._idn, "emissivity": self._emissivity}
        if len(header) == FRAME_HEADER:
            t_max_raw, t_min_raw = (int(v) for v in np.frombuffer(header, dtype="<u2"))
            # Display-range extrema from the device, not min/max of the raw grid.
            meta["device_t_max_raw"] = t_max_raw
            meta["device_t_min_raw"] = t_min_raw
        if frames_unique is not None:
            meta["frames_unique"] = int(frames_unique)
        if frames_received is not None:
            meta["frames_received"] = int(frames_received)
        return meta

    def _to_frame(
        self,
        raw: bytes,
        header: bytes = b"",
        *,
        frames_unique: int | None = None,
        frames_received: int | None = None,
    ) -> Frame2D:
        return Frame2D(
            values=self._decode_counts(raw),
            unit="count",
            quantity=QuantityKind.TEMPERATURE,
            timestamp=datetime.now(timezone.utc),
            mask=None if self._mask is None else self._mask.copy(),
            calibration=None,
            metadata=self._frame_metadata(
                header, frames_unique=frames_unique, frames_received=frames_received
            ),
        )


def _parse_emissivity(text: str) -> float:
    match = _EMISSIVITY_RE.search(text)
    if not match:
        raise DecodeError(f"no emissivity in {text!r}")
    raw = float(match.group(1))
    if not 0 < raw <= 100:
        raise DecodeError(f"emissivity out of range in {text!r}")
    return raw / 100.0


def _parse_bad_pixels(text: str) -> list[tuple[int, int]]:
    """Parse ``(x, y)`` pairs. Firmware lists column, row; stored as (row, col)."""
    out: list[tuple[int, int]] = []
    for x_s, y_s in _BADPIX_RE.findall(text):
        x, y = int(x_s), int(y_s)
        out.append((y, x))
    return out


def _mask_from_bad(pixels: list[tuple[int, int]]) -> np.ndarray:
    mask = np.zeros((FRAME_H, FRAME_W), dtype=bool)
    for row, col in pixels:
        if 0 <= row < FRAME_H and 0 <= col < FRAME_W:
            mask[row, col] = True
    return mask


def _usb_serial(address: str) -> str | None:
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    for port in list_ports.comports():
        if port.device == address and port.serial_number:
            return str(port.serial_number)
    return None
