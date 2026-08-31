"""UNI-T UT372 digital tachometer driver.

The UT372 streams LCD segment packets over a WCH CH9325 HID-UART (2400 8N1).
Mode (RPM / COUNT) and MAX/MIN/AVG/HOLD are set on the front panel — there is
no command channel — so this driver implements only ``ScalarReader``.

Packet format (sigrok / libsigrok ``ut372.c``): 27 ASCII bytes ending in CR LF.
Pairs of characters decode to 7-segment bitfields; the first character is
ignored. The parser is a pure ``UT372Decoder`` so it can be tested without HID.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from ..base import Instrument, run_with_retry
from ..errors import DecodeError, TimeoutInstrumentError
from ..hid_ch9325 import DEFAULT_BAUD, Ch9325HidTransport
from ..types import InstrumentInfo, QuantityKind, Reading, RetryPolicy

DRIVER_NAME = "uni_t_ut372"

PACKET_LEN = 27
EOL = b"\r\n"
DIGIT_LOOKUP = (0x7B, 0x60, 0x5E, 0x7C, 0x65, 0x3D, 0x3F, 0x70, 0x7F, 0x7D)
DIGIT_BY_GLYPH = {pat: i for i, pat in enumerate(DIGIT_LOOKUP)}
DIGIT_BLANK = 0x00
DIGIT_L = 0x0B
DECIMAL_POINT = 0x80

FLAGS1_HOLD = 1 << 2
FLAGS1_BATT = 1 << 1
FLAGS1_LED = 1 << 4
FLAGS2_RPM = 1 << 0
FLAGS2_COUNT = 1 << 1
FLAGS2_MAX = 1 << 4
FLAGS2_MIN = 1 << 5
FLAGS2_AVG = 1 << 6


def encode_hex_pair(value: int) -> bytes:
    """Inverse of the UT372 nibble transform (test / emulator helper)."""
    hexstr = f"{value & 0xFF:02X}"
    out = bytearray()
    for ch in hexstr:
        code = ord(ch)
        out.append(code - 7 if code > 0x39 else code)
    return bytes(out)


def decode_hex_pair(buf: bytes) -> int:
    if len(buf) != 2:
        raise DecodeError(f"expected 2-byte pair, got {len(buf)}")
    hex_chars = bytearray(buf)
    for i, ch in enumerate(hex_chars):
        if ch > 0x39:
            hex_chars[i] = ch + 7
    try:
        return int(hex_chars.decode("ascii"), 16)
    except ValueError as exc:
        raise DecodeError(f"invalid hex pair {buf!r}") from exc


def _decode_digits(pairs: bytes, *, allow_empty: bool = False) -> tuple[float, bool]:
    """Five LSD-first segment pairs → (value, overload).

    Allowed glyphs: 0–9 (LUT), blank (``0x00``, MSD suffix only), or ``L``
    (overload). Unknown patterns raise so a misaligned window cannot silently
    decode as 0.
    """
    if len(pairs) != 10:
        raise DecodeError(f"expected 10 digit bytes, got {len(pairs)}")
    value = 0
    exponent = 0
    overload = False
    seen_blank = False
    have_digit = False
    for i in range(5):
        segments = decode_hex_pair(pairs[2 * i : 2 * i + 2])
        glyph = segments & ~DECIMAL_POINT
        if glyph == DIGIT_L:
            overload = True
        elif glyph == DIGIT_BLANK:
            seen_blank = True
        else:
            if seen_blank:
                raise DecodeError("blank digit below a significant digit")
            digit = DIGIT_BY_GLYPH.get(glyph)
            if digit is None:
                raise DecodeError(f"unknown UT372 glyph 0x{glyph:02x}")
            have_digit = True
            value += digit * (10**i)
        if segments & DECIMAL_POINT:
            exponent = -i
    if not have_digit and not overload:
        if allow_empty:
            return 0.0, False
        raise DecodeError("UT372 digit field is empty")
    return float(value) * (10.0**exponent), overload


class UT372Decoder:
    """Pure decoder for the 27-byte UT372 LCD packet (no I/O)."""

    def decode(self, raw: bytes, *, allow_short: bool = False) -> dict:
        if allow_short and raw.endswith(EOL) and len(raw) == PACKET_LEN - 1:
            # CH9325 sometimes drops the ignored first character.
            raw = b"0" + raw
        if len(raw) != PACKET_LEN:
            raise DecodeError(f"expected {PACKET_LEN} bytes, got {len(raw)}")
        if raw[25:27] != EOL:
            raise DecodeError("UT372 packet missing CR LF")

        flags1 = decode_hex_pair(raw[21:23])
        flags2 = decode_hex_pair(raw[23:25])
        rpm = bool(flags2 & FLAGS2_RPM)
        count = bool(flags2 & FLAGS2_COUNT)
        if not rpm and not count:
            raise DecodeError("UT372 setup menu (no RPM/COUNT)")

        value, overload = _decode_digits(raw[1:11])
        # HOLD turns the time digits off; treat an empty time field as 0.
        time_value, _ = _decode_digits(raw[11:21], allow_empty=True)
        unit = "rpm" if rpm else "count"
        quantity = QuantityKind.RPM if rpm else QuantityKind.COUNT
        if overload:
            value = float("inf")

        return {
            "mode": "rpm" if rpm else "count",
            "val": value,
            "units": unit,
            "norm_val": None if overload else value,
            "norm_units": unit,
            "time_val": time_value,
            "hold": bool(flags1 & FLAGS1_HOLD),
            "batt": bool(flags1 & FLAGS1_BATT),
            "led": bool(flags1 & FLAGS1_LED),
            "rpm": rpm,
            "count": count,
            "max": bool(flags2 & FLAGS2_MAX),
            "min": bool(flags2 & FLAGS2_MIN),
            "avg": bool(flags2 & FLAGS2_AVG),
            "ovl": overload,
            "quantity": quantity,
        }


_FLAG_KEYS = ("hold", "batt", "led", "rpm", "count", "max", "min", "avg", "ovl")


class UT372Tach(Instrument):
    """UT372 tachometer exposed as a ScalarReader (role ``tach``)."""

    def __init__(
        self,
        name: str,
        address: str,
        *,
        retry: RetryPolicy | None = None,
        baud: int = DEFAULT_BAUD,
        timeout_s: float = 8.0,
        exclusive: bool = True,
        transport: Ch9325HidTransport | None = None,
    ) -> None:
        super().__init__(name, address, retry=retry)
        self._timeout_s = timeout_s
        self._t = transport or Ch9325HidTransport(
            address,
            baud=baud,
            timeout_s=timeout_s,
            exclusive=exclusive,
        )
        self._decoder = UT372Decoder()

    @property
    def info(self) -> InstrumentInfo:
        return InstrumentInfo(
            driver=DRIVER_NAME,
            address=self._address,
            transport="hid_ch9325",
            idn="UNI-T UT372 (HID telemetry)",
        )

    def connect(self) -> None:
        if not self._t.is_open:
            self._t.open()

    def disconnect(self) -> None:
        self._t.close()

    def _read_packet(self) -> dict:
        """Assemble and decode one LCD frame within a single timeout budget."""
        deadline = time.monotonic() + self._timeout_s
        acc = bytearray()
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            piece = self._t.read_until(
                EOL,
                max_bytes=PACKET_LEN + 8,
                timeout_s=remaining,
            )
            if piece:
                acc.extend(piece)
            parsed = self._frame_from_buffer(acc)
            if parsed is not None:
                return parsed
            if not piece and not acc:
                raise TimeoutInstrumentError(
                    "no UT372 UART data (CH9325 HID is up). Power on the meter "
                    "and set setup USB=1 (HOLD to enter setup; USB is off after every restart)."
                )
        raise DecodeError(f"could not sync UT372 frame ({len(acc)} bytes)")

    def _frame_from_buffer(self, acc: bytearray) -> dict | None:
        end = 0
        while True:
            idx = acc.find(EOL, end)
            if idx < 0:
                return None
            for length, allow_short in ((PACKET_LEN, False), (PACKET_LEN - 1, True)):
                start = idx + len(EOL) - length
                if start < 0:
                    continue
                cand = bytes(acc[start : idx + len(EOL)])
                try:
                    return self._decoder.decode(cand, allow_short=allow_short)
                except DecodeError:
                    continue
            end = idx + len(EOL)

    def read(self) -> Reading:
        parsed = run_with_retry(
            self.retry,
            self._read_packet,
            op="ut372.read",
        )
        flags = {k: bool(parsed.get(k)) for k in _FLAG_KEYS}
        value = parsed["val"]
        return Reading(
            value=float(value) if value is not None else float("nan"),
            unit=parsed["units"] or "",
            quantity=parsed["quantity"],
            timestamp=datetime.now(timezone.utc),
            normalized_value=parsed["norm_val"],
            normalized_unit=parsed["norm_units"],
            flags=flags,
            raw=parsed,
        )
