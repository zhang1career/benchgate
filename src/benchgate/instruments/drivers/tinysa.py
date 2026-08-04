"""tinySA spectrum analyzer / signal generator driver.

USB CDC serial console (product string ``tinySA``) speaking the text protocol
documented at https://tinysa.org/wiki/pmwiki.php?n=Main.USBInterface.

Implements ``SpectrumAnalyzer`` and ``RFSource``. Does **not** implement
``VectorAnalyzer`` (no S-parameter calibration / history like HTOOL SA8).

Wire protocol:
  * 115200 8N1 (baud ignored by USB CDC); commands end with ``\\r``.
  * Responses end at the ``ch> `` prompt.
  * Spectrum capture uses ``scanraw`` binary framing:
    ``{`` + (``x`` + uint16_le)*N + ``}`` → dBm = raw/32 - offset
    (offset 128 for classic tinySA, 174 for Ultra).
"""

from __future__ import annotations

import re
import struct
import time
from datetime import datetime, timezone

import numpy as np

from ..base import Instrument, run_with_retry
from ..errors import (
    DecodeError,
    InstrumentConnectionError,
    InstrumentError,
    TimeoutInstrumentError,
    TransientInstrumentError,
)
from ..transport import SerialTransport
from ..types import InstrumentInfo, PeakMode, QuantityKind, Reading, RetryPolicy, ScanConfig, Spectrum

DRIVER_NAME = "tinysa"

DEFAULT_BAUD = 115200
DEFAULT_PROMPT = "ch>"
DEFAULT_POINTS = 290
DEFAULT_TIMEOUT_S = 5.0
# Official wiki: classic tinySA subtracts 128; Ultra / Ultra+ subtracts 174.
DBM_OFFSET_CLASSIC = 128.0
DBM_OFFSET_ULTRA = 174.0

_USB_PRODUCT = "tinySA"


def resolve_tinysa_address(address: str) -> str:
    """Resolve a serial path or USB product name (``tinySA``) to a device path."""
    text = address.strip()
    if not text:
        raise InstrumentConnectionError("tinySA address is empty")
    if text.startswith("/dev/") or text.upper().startswith("COM") or text.startswith("tty"):
        return text

    try:
        from serial.tools import list_ports
    except ImportError as exc:  # pragma: no cover
        raise InstrumentConnectionError(
            "pyserial is required; install with: pip install benchgate[lab]"
        ) from exc

    needle = text.casefold()
    matches = [
        p.device
        for p in list_ports.comports()
        if needle in (p.product or "").casefold()
        or needle in (p.description or "").casefold()
        or needle == (p.manufacturer or "").casefold()
    ]
    # Prefer exact product match when several CDC devices share the STM VID.
    exact = [
        p.device
        for p in list_ports.comports()
        if (p.product or "") == text or (p.description or "") == text
    ]
    chosen = exact or matches
    if not chosen:
        raise InstrumentConnectionError(
            f"No serial port with USB product/description matching {address!r}. "
            "Plug in the tinySA or set address to the cu.*/ttyACM* path."
        )
    if len(set(chosen)) > 1:
        raise InstrumentConnectionError(
            f"Multiple ports match {address!r}: {sorted(set(chosen))}. "
            "Set a concrete serial path in instruments.yaml."
        )
    return chosen[0]


def decode_scanraw_payload(payload: bytes, *, points: int, dbm_offset: float) -> np.ndarray:
    """Decode ``scanraw`` body (between ``{`` and ``}``) to amplitude dBm."""
    expected = points * 3
    if len(payload) < expected:
        raise DecodeError(f"scanraw short payload: got {len(payload)} bytes, need {expected}")
    body = payload[:expected]
    try:
        values = struct.unpack("<" + "xH" * points, body)
    except struct.error as exc:
        raise DecodeError(f"scanraw unpack failed: {exc}") from exc
    raw = np.asarray(values, dtype=np.float64)
    return raw / 32.0 - float(dbm_offset)


def parse_marker_peak_line(text: str) -> tuple[float, float]:
    """Parse ``marker N peak`` payload like ``1 0 1000000 -3.04e+01`` → (dbm, freq_hz)."""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.endswith(">") or s.lower().startswith("marker"):
            continue
        parts = s.split()
        if len(parts) >= 4:
            try:
                freq_hz = float(parts[2])
                level_dbm = float(parts[3])
                return level_dbm, freq_hz
            except ValueError:
                continue
    raise DecodeError(f"could not parse marker peak response: {text!r}")


def parse_sweep_status(text: str) -> tuple[float, float, int]:
    """Parse ``sweep`` response ``start stop points`` (Hz)."""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.endswith(">") or s.lower().startswith("sweep"):
            continue
        parts = s.split()
        if len(parts) >= 3:
            try:
                return float(parts[0]), float(parts[1]), int(float(parts[2]))
            except ValueError:
                continue
    raise DecodeError(f"could not parse sweep status: {text!r}")


def _mhz_to_hz(mhz: float) -> int:
    return int(round(mhz * 1e6))


def _infer_dbm_offset(info_text: str) -> float:
    low = info_text.casefold()
    if "ultra" in low or "tinysa4" in low:
        return DBM_OFFSET_ULTRA
    return DBM_OFFSET_CLASSIC


class TinySA(Instrument):
    def __init__(
        self,
        name: str,
        address: str,
        *,
        baud: int = DEFAULT_BAUD,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        points: int = DEFAULT_POINTS,
        dbm_offset: float | None = None,
        prompt: str = DEFAULT_PROMPT,
        retry: RetryPolicy | None = None,
        transport: SerialTransport | None = None,
    ) -> None:
        resolved = address if transport is not None else resolve_tinysa_address(address)
        super().__init__(name, resolved, retry=retry)
        self._requested_address = address
        self.prompt = prompt.rstrip()
        self.timeout_s = timeout_s
        self.points = max(2, min(int(points), 450))
        self._dbm_offset_override = dbm_offset
        self._dbm_offset = float(dbm_offset) if dbm_offset is not None else DBM_OFFSET_CLASSIC
        self._t = transport or SerialTransport(
            resolved, baud=baud, bytesize=8, parity="N", stopbits=1, timeout_s=0.2
        )
        self._idn = ""
        self._start_hz: float | None = None
        self._stop_hz: float | None = None
        self._gen_enabled = False
        self._gen_freq_mhz: float | None = None
        self._gen_power_dbm: int | None = None

    @property
    def info(self) -> InstrumentInfo:
        meta = {"usb_product": _USB_PRODUCT}
        if self._requested_address != self._address:
            meta["requested_address"] = self._requested_address
        return InstrumentInfo(
            driver=DRIVER_NAME,
            address=self._address,
            transport="serial",
            idn=self._idn,
            metadata=meta,
        )

    def connect(self) -> None:
        if not self._t.is_open:
            self._t.open()
        self._t.flush_input()
        # Nudge the console; firmware echoes and ends at ch>
        self._t.write("\r")
        ready = self._t.read_until_text(self.prompt, deadline_s=min(2.0, self.timeout_s))
        if self.prompt not in ready:
            raise InstrumentConnectionError(
                f"tinySA prompt {self.prompt!r} not seen on {self._address}"
            )
        info = self._send("info", deadline_s=self.timeout_s)
        self._idn = self._info_identity(info)
        if self._dbm_offset_override is None:
            self._dbm_offset = _infer_dbm_offset(info)
        # Prefer input mode for spectrum work after connect.
        self._send("mode low input", deadline_s=self.timeout_s)

    def disconnect(self) -> None:
        self._t.close()

    def identify(self) -> str:
        return self._idn

    # --- SpectrumAnalyzer ---

    def configure_scan(self, config: ScanConfig) -> None:
        def _do() -> None:
            if config.center_mhz is not None and config.span_mhz is not None:
                center = _mhz_to_hz(config.center_mhz)
                span = _mhz_to_hz(config.span_mhz)
                half = span // 2
                self._start_hz = float(center - half)
                self._stop_hz = float(center + half)
                self._send(f"sweep center {center}")
                self._send(f"sweep span {span}")
            elif config.start_mhz is not None and config.stop_mhz is not None:
                self._start_hz = float(_mhz_to_hz(config.start_mhz))
                self._stop_hz = float(_mhz_to_hz(config.stop_mhz))
                self._send(f"sweep start {int(self._start_hz)}")
                self._send(f"sweep stop {int(self._stop_hz)}")
            else:
                if config.center_mhz is not None:
                    self._send(f"sweep center {_mhz_to_hz(config.center_mhz)}")
                if config.span_mhz is not None:
                    self._send(f"sweep span {_mhz_to_hz(config.span_mhz)}")
                if config.start_mhz is not None:
                    self._start_hz = float(_mhz_to_hz(config.start_mhz))
                    self._send(f"sweep start {int(self._start_hz)}")
                if config.stop_mhz is not None:
                    self._stop_hz = float(_mhz_to_hz(config.stop_mhz))
                    self._send(f"sweep stop {int(self._stop_hz)}")

            if config.attenuation is not None:
                if config.attenuation < 0:
                    self._send("attenuate auto")
                else:
                    att = max(0, min(31, int(config.attenuation)))
                    self._send(f"attenuate {att}")
            if config.reference_dbm is not None:
                self._send(f"trace reflevel {config.reference_dbm}")

            # Refresh cached range from device when only partial fields were set.
            if self._start_hz is None or self._stop_hz is None:
                start, stop, _pts = parse_sweep_status(self._send("sweep"))
                self._start_hz = start
                self._stop_hz = stop

        run_with_retry(self.retry, _do, op="tinysa.configure_scan")

    def capture_spectrum(self) -> Spectrum:
        def _do() -> Spectrum:
            start_hz, stop_hz, points = self._resolve_scan_window()
            self._send("pause")
            try:
                raw = self._send_raw(
                    f"scanraw {int(start_hz)} {int(stop_hz)} {points}",
                    deadline_s=max(self.timeout_s, 2.0 + points / 100.0),
                )
                payload = self._extract_scanraw_payload(raw)
                amps = decode_scanraw_payload(payload, points=points, dbm_offset=self._dbm_offset)
            finally:
                try:
                    self._send("resume")
                except InstrumentError:
                    pass
            freq = np.linspace(start_hz, stop_hz, points, dtype=np.float64)
            return Spectrum(
                freq_hz=freq,
                amplitude_dbm=amps,
                timestamp=datetime.now(timezone.utc),
                trace="current",
                metadata={
                    "start_mhz": start_hz / 1e6,
                    "stop_mhz": stop_hz / 1e6,
                    "points": points,
                    "dbm_offset": self._dbm_offset,
                },
            )

        return run_with_retry(self.retry, _do, op="tinysa.capture_spectrum")

    def measure_peak(self, mode: PeakMode = PeakMode.AVR) -> Reading:
        def _do() -> Reading:
            # Firmware marker peak finds the strongest bin; PeakMode is recorded only.
            text = self._send("marker 1 peak")
            value_dbm, freq_hz = parse_marker_peak_line(text)
            return Reading(
                value=value_dbm,
                unit="dBm",
                quantity=QuantityKind.UNKNOWN,
                timestamp=datetime.now(timezone.utc),
                raw={"requested_mode": mode.value, "freq_hz": freq_hz, "freq_mhz": freq_hz / 1e6},
            )

        return run_with_retry(self.retry, _do, op="tinysa.measure_peak")

    def measure_floor(self) -> Reading:
        def _do() -> Reading:
            spec = self.capture_spectrum()
            value = float(np.min(spec.amplitude_dbm)) if len(spec) else float("nan")
            return Reading(
                value=value,
                unit="dBm",
                quantity=QuantityKind.UNKNOWN,
                timestamp=datetime.now(timezone.utc),
                raw={"points": len(spec), "method": "min_scanraw"},
            )

        return run_with_retry(self.retry, _do, op="tinysa.measure_floor")

    # --- RFSource ---

    def set_generator_enabled(self, enabled: bool) -> None:
        def _do() -> None:
            if enabled:
                self._send("mode low output")
                self._send("output on")
            else:
                self._send("output off")
                self._send("mode low input")
            self._gen_enabled = bool(enabled)

        run_with_retry(self.retry, _do, op="tinysa.gen_enable")

    def set_generator_frequency_mhz(self, freq_mhz: float) -> None:
        hz = _mhz_to_hz(freq_mhz)
        run_with_retry(self.retry, lambda: self._send(f"freq {hz}"), op="tinysa.gen_freq")
        self._gen_freq_mhz = float(freq_mhz)

    def set_generator_power_dbm(self, power_dbm: int) -> None:
        # Classic low-output range is roughly -76..-6 dBm (device clamps).
        run_with_retry(self.retry, lambda: self._send(f"level {int(power_dbm)}"), op="tinysa.gen_power")
        self._gen_power_dbm = int(power_dbm)

    def set_generator_attenuator(self, att: int) -> None:
        # No separate digital TG attenuator; map to input attenuate for symmetry
        # with shared lab_sa_gen options when still in input mode.
        att_i = max(0, min(31, int(att)))
        run_with_retry(self.retry, lambda: self._send(f"attenuate {att_i}"), op="tinysa.gen_att")

    def query_generator_enabled(self) -> bool:
        return self._gen_enabled

    def query_generator_frequency_mhz(self) -> float:
        if self._gen_freq_mhz is None:
            raise InstrumentError("generator frequency has not been set yet")
        return self._gen_freq_mhz

    # --- internals ---

    def _resolve_scan_window(self) -> tuple[float, float, int]:
        if self._start_hz is not None and self._stop_hz is not None:
            return self._start_hz, self._stop_hz, self.points
        start, stop, pts = parse_sweep_status(self._send("sweep"))
        self._start_hz = start
        self._stop_hz = stop
        return start, stop, min(self.points, pts) if pts > 0 else self.points

    def _send(self, cmd: str, *, deadline_s: float | None = None) -> str:
        raw = self._send_raw(cmd, deadline_s=deadline_s)
        return raw.decode("ascii", errors="replace")

    def _send_raw(self, cmd: str, *, deadline_s: float | None = None) -> bytes:
        deadline = self.timeout_s if deadline_s is None else deadline_s

        def _do() -> bytes:
            self._t.flush_input()
            self._t.write(cmd.rstrip("\r\n") + "\r")
            end = time.monotonic() + deadline
            buf = bytearray()
            prompt = self.prompt.encode("ascii")
            while time.monotonic() < end:
                chunk = self._t.read(256)
                if chunk:
                    buf.extend(chunk)
                    if prompt in buf:
                        return bytes(buf)
                else:
                    time.sleep(0.01)
            raise TimeoutInstrumentError(f"no {self.prompt!r} after {cmd!r}")

        try:
            return run_with_retry(self.retry, _do, op=f"tinysa.send:{cmd.split()[0]}")
        except InstrumentError:
            raise
        except Exception as exc:
            raise TransientInstrumentError(str(exc)) from exc

    @staticmethod
    def _extract_scanraw_payload(raw: bytes) -> bytes:
        start = raw.find(b"{")
        end = raw.rfind(b"}")
        if start < 0 or end < 0 or end <= start:
            raise DecodeError(f"scanraw framing missing in {raw[:80]!r}...")
        return raw[start + 1 : end]

    @staticmethod
    def _info_identity(info_text: str) -> str:
        lines = [ln.strip() for ln in info_text.splitlines() if ln.strip()]
        # Drop echo / prompt lines.
        payload = [ln for ln in lines if ln.lower() != "info" and not ln.endswith(">")]
        if not payload:
            return "tinySA"
        # Prefer "tinySA v0.3" style first line + version if present.
        head = payload[0]
        ver = next((ln for ln in payload if ln.lower().startswith("version:")), "")
        if ver:
            ver_m = re.sub(r"^version:\s*", "", ver, flags=re.I)
            return f"{head}, {ver_m}"
        return head
