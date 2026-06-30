"""UNI-T UT61E digital multimeter driver.

The UT61E is a *passive telemetry* device: over its IR/RS-232 adapter it streams
14-byte Cyrustek ES51922 packets at a fixed rate. There is no command channel —
mode/range are set on the device front panel — so this driver implements only
``ScalarReader``.

The packet parser is split out as a pure, I/O-free ``UT61EDecoder`` so it can be
unit-tested against recorded frames. Ported from the standalone adapter; the
original ``low_bat`` bit-mask bug is fixed here and temperature / unsupported
ranges are handled instead of crashing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..base import Instrument, run_with_retry
from ..errors import DecodeError
from ..transport import SerialTransport
from ..types import InstrumentInfo, QuantityKind, Reading, RetryPolicy

DRIVER_NAME = "uni_t_ut61e"

# --- Serial / framing constants (ES51922 over the UT61E IR adapter) ---
BAUD_RATE = 19200
BYTESIZE = 7
PARITY = "O"
STOPBITS = 1
TIMEOUT_S = 1.0
DTR = True
RTS = False
EOL = b"\x0d\x0a"
RAW_LEN = 14

# --- Protocol bit masks ---
DIGIT_MASK = 0b00001111
DIGIT_BYTES = (1, 2, 3, 4, 5)
PERCENT = 0b00001000  # byte 7 bit 3
NEG = 0b00000100      # byte 7 bit 2
LOW_BAT = 0b00000010  # byte 7 bit 1
OL = 0b00000001       # byte 7 bit 0
DELTA = 0b00000010    # byte 8 bit 1
UL = 0b00001000       # byte 9 bit 3
MAX = 0b00000100      # byte 9 bit 2
MIN = 0b00000010      # byte 9 bit 1
DC = 0b00001000       # byte 10 bit 3
AC = 0b00000100       # byte 10 bit 2
AUTO = 0b00000010     # byte 10 bit 1
HZ = 0b00000001       # byte 10 bit 0
HOLD = 0b00000010     # byte 11 bit 1

RANGE_V = (
    ("2.2000", "V", 0.0001),
    ("22.000", "V", 0.001),
    ("220.00", "V", 0.01),
    ("1000.0", "V", 0.1),
    ("220.00", "mV", 0.01),
)
RANGE_R = (
    ("220.00", "Ohm", 0.01),
    ("2.2000", "kOhm", 0.0001),
    ("22.000", "kOhm", 0.001),
    ("220.00", "kOhm", 0.01),
    ("2.2000", "MOhm", 0.0001),
    ("22.000", "MOhm", 0.001),
    ("220.00", "MOhm", 0.01),
)
RANGE_C = (
    ("22.000", "nF", 0.001),
    ("220.00", "nF", 0.01),
    ("2.2000", "uF", 0.0001),
    ("22.000", "uF", 0.001),
    ("220.00", "uF", 0.01),
    ("2.2000", "mF", 0.0001),
    ("22.000", "mF", 0.001),
    ("220.00", "mF", 0.01),
)
RANGE_F = (
    ("220.00", "Hz", 0.01),
    ("2200.0", "Hz", 0.1),
    None,
    ("22.000", "kHz", 0.001),
    ("220.00", "kHz", 0.01),
    ("2.2000", "MHz", 0.0001),
    ("22.000", "MHz", 0.001),
    ("220.00", "MHz", 0.01),
)
RANGE_I_UA = (("220.00", "uA", 0.01), ("2200.0", "uA", 0.1))
RANGE_I_MA = (("22.000", "mA", 0.001), ("220.00", "mA", 0.01))
RANGE_I_A = (("10.000", "A", 0.001),)
RANGE_PERCENT = (
    ("100.0", "%", 0.01),
    ("100.0", "%", 0.01),
    None,
    ("100.0", "%", 0.01),
    ("100.0", "%", 0.01),
    ("100.0", "%", 0.01),
    ("100.0", "%", 0.01),
)

MEAS_TYPE = (
    ("A", RANGE_I_A),
    ("Diode", RANGE_V),
    ("Hz/%", RANGE_F),
    ("Ohm", RANGE_R),
    ("deg", None),
    ("Buzzer", RANGE_R),
    ("Cap", RANGE_C),
    None,
    None,
    ("A", RANGE_I_A),
    None,
    ("V/mV", RANGE_V),
    None,
    ("uA", RANGE_I_UA),
    ("ADP", None),
    ("mA", RANGE_I_MA),
)

NORM_RULES = {
    "V": (1, "V"),
    "mV": (1e-03, "V"),
    "A": (1, "A"),
    "mA": (1e-03, "A"),
    "uA": (1e-06, "A"),
    "Ohm": (1, "Ohm"),
    "kOhm": (1e03, "Ohm"),
    "MOhm": (1e06, "Ohm"),
    "nF": (1e-9, "F"),
    "uF": (1e-6, "F"),
    "mF": (1e-3, "F"),
    "Hz": (1, "Hz"),
    "kHz": (1e03, "Hz"),
    "MHz": (1e06, "Hz"),
    "%": (1, "%"),
}

_MODE_QUANTITY = {
    "V/mV": QuantityKind.VOLTAGE,
    "Diode": QuantityKind.VOLTAGE,
    "A": QuantityKind.CURRENT,
    "mA": QuantityKind.CURRENT,
    "uA": QuantityKind.CURRENT,
    "Ohm": QuantityKind.RESISTANCE,
    "Buzzer": QuantityKind.RESISTANCE,
    "Cap": QuantityKind.CAPACITANCE,
    "Hz/%": QuantityKind.FREQUENCY,
    "deg": QuantityKind.TEMPERATURE,
    "ADP": QuantityKind.UNKNOWN,
}


class UT61EDecoder:
    """Pure decoder for ES51922 14-byte frames (no I/O)."""

    @staticmethod
    def normalize(value: float, unit: str) -> tuple[float, str]:
        mult, target = NORM_RULES.get(unit, (1, unit))
        return value * mult, target

    def decode(self, raw: list[int]) -> dict:
        if len(raw) != RAW_LEN:
            raise DecodeError(f"expected {RAW_LEN} bytes, got {len(raw)}")

        res: dict = {
            "mode": None,
            "range": None,
            "val": None,
            "units": None,
            "norm_val": None,
            "norm_units": None,
            "percent": bool(raw[7] & PERCENT),
            "minus": bool(raw[7] & NEG),
            "low_bat": bool(raw[7] & LOW_BAT),
            "ovl": bool(raw[7] & OL),
            "delta": bool(raw[8] & DELTA),
            "ul": bool(raw[9] & UL),
            "max": bool(raw[9] & MAX),
            "min": bool(raw[9] & MIN),
            "dc": bool(raw[10] & DC),
            "ac": bool(raw[10] & AC),
            "auto": bool(raw[10] & AUTO),
            "hz": bool(raw[10] & HZ),
            "hold": bool(raw[11] & HOLD),
            "data_valid": True,
        }

        meas_type = MEAS_TYPE[raw[6] & 0x0F]
        if meas_type is None:
            res["mode"] = "unknown"
            return res
        res["mode"] = meas_type[0]

        range_id = raw[0] & 0b00000111
        if res["percent"]:
            table = RANGE_PERCENT
        elif res["hz"]:
            table = RANGE_F
        else:
            table = meas_type[1]

        meas_range = table[range_id] if table is not None and range_id < len(table) else None
        if meas_range is None:
            # e.g. temperature mode, or a range slot the device does not use.
            return res

        res["range"] = meas_range[0]
        res["units"] = meas_range[1]
        multiplier = meas_range[2]

        digits = 0
        for n in DIGIT_BYTES:
            digits = digits * 10 + (raw[n] & DIGIT_MASK)
        val = digits * multiplier
        if res["minus"]:
            val = -val
        res["val"] = val
        res["norm_val"], res["norm_units"] = self.normalize(val, res["units"])
        return res


_FLAG_KEYS = ("percent", "minus", "low_bat", "ovl", "delta", "ul", "max", "min", "dc", "ac", "auto", "hz", "hold")


class UT61EDmm(Instrument):
    """UT61E DMM exposed as a ScalarReader."""

    def __init__(
        self,
        name: str,
        address: str,
        *,
        retry: RetryPolicy | None = None,
        transport: SerialTransport | None = None,
    ) -> None:
        super().__init__(name, address, retry=retry)
        self._t = transport or SerialTransport(
            address,
            baud=BAUD_RATE,
            bytesize=BYTESIZE,
            parity=PARITY,
            stopbits=STOPBITS,
            timeout_s=TIMEOUT_S,
            dtr=DTR,
            rts=RTS,
        )
        self._decoder = UT61EDecoder()
        self._connected = False

    @property
    def info(self) -> InstrumentInfo:
        return InstrumentInfo(
            driver=DRIVER_NAME,
            address=self._address,
            transport="serial",
            idn="UNI-T UT61E (serial telemetry)",
        )

    def connect(self) -> None:
        if not self._t.is_open:
            self._t.open()
        self._connected = True

    def disconnect(self) -> None:
        self._t.close()
        self._connected = False

    def _read_packet(self) -> list[int]:
        """One fresh packet; raises a transient error so the policy can retry."""
        self._t.flush_input()
        frame = self._t.read_until(EOL, max_bytes=RAW_LEN)
        if len(frame) != RAW_LEN or not frame.endswith(EOL):
            raise DecodeError(f"short/garbled frame ({len(frame)} bytes)")
        return list(frame)

    def read(self) -> Reading:
        parsed = run_with_retry(
            self.retry,
            lambda: self._decoder.decode(self._read_packet()),
            op="ut61e.read",
        )
        flags = {k: bool(parsed.get(k)) for k in _FLAG_KEYS}
        value = parsed["val"]
        return Reading(
            value=float(value) if value is not None else float("nan"),
            unit=parsed["units"] or "",
            quantity=_MODE_QUANTITY.get(parsed["mode"], QuantityKind.UNKNOWN),
            timestamp=datetime.now(timezone.utc),
            normalized_value=parsed["norm_val"],
            normalized_unit=parsed["norm_units"],
            flags=flags,
            raw=parsed,
        )
