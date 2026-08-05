"""HTOOL SA8 spectrum analyzer / tracking generator / scalar VNA driver.

USB CDC-ACM serial link (``GD32-CDC_ACM``) carrying SCPI. The first command after
connect **must** be ``*IDN?`` to enter SCPI mode.

Implements ``SpectrumAnalyzer``, ``RFSource``, and ``VectorAnalyzer``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from ..base import Instrument, run_with_retry
from ..errors import InstrumentConnectionError
from ..scpi import (
    SA8_SWEEP_TOTAL_BYTES,
    decode_sa8_history_payload,
    decode_sa8_sweep_payload,
    parse_floor_response,
    parse_labeled_value,
    parse_peak_response,
)
from ..transport import ScpiChannel, SerialScpiTransport
from ..types import (
    CalStandard,
    InstrumentInfo,
    PeakMode,
    QuantityKind,
    Reading,
    RetryPolicy,
    ScanConfig,
    Spectrum,
    SparamKind,
)

DRIVER_NAME = "htool_sa8"

DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT_S = 3.0


def _parse_float(text: str, *labels: str) -> float:
    return float(parse_labeled_value(text, *labels))


def _mhz_to_hz(mhz: float) -> float:
    return mhz * 1e6


class HtoolSA8(Instrument):
    def __init__(
        self,
        name: str,
        address: str,
        *,
        baud: int = DEFAULT_BAUD,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retry: RetryPolicy | None = None,
        transport: ScpiChannel | None = None,
    ) -> None:
        super().__init__(name, address, retry=retry)
        self._t = transport or SerialScpiTransport(address, baud=baud, timeout_s=timeout_s)
        self._idn = ""

    @property
    def info(self) -> InstrumentInfo:
        return InstrumentInfo(
            driver=DRIVER_NAME,
            address=self._address,
            transport="serial_scpi",
            idn=self._idn,
        )

    def connect(self) -> None:
        self._t.open()
        if hasattr(self._t, "prime_scpi"):
            self._idn = run_with_retry(self.retry, lambda: self._t.prime_scpi(), op="sa8.idn")  # type: ignore[attr-defined]
        else:
            self._idn = run_with_retry(self.retry, lambda: self._t.query("*IDN?").strip(), op="sa8.idn")
        if "HTOOL" not in self._idn.upper() and "," not in self._idn:
            raise InstrumentConnectionError(
                f"SA8 did not return a valid *IDN? ({self._idn!r}). "
                "Power-cycle the device and ensure no other app used the custom protocol."
            )

    def disconnect(self) -> None:
        self._t.close()

    def identify(self) -> str:
        return self._idn

    # --- SpectrumAnalyzer ---

    def configure_scan(self, config: ScanConfig) -> None:
        def _do() -> None:
            if config.center_mhz is not None and config.span_mhz is not None:
                self._t.write(f"FREQuency:SCAN:CENTer {config.center_mhz},{config.span_mhz}")
            elif config.start_mhz is not None and config.stop_mhz is not None:
                self._t.write(f"FREQuency:SCAN:RANGe {config.start_mhz},{config.stop_mhz}")
            if config.center_mhz is not None and config.span_mhz is None:
                self._t.write(f"FREQuency:CENTer {config.center_mhz}")
            if config.span_mhz is not None and config.center_mhz is None:
                self._t.write(f"FREQuency:SPAN {config.span_mhz}")
            if config.start_mhz is not None and config.stop_mhz is None:
                self._t.write(f"FREQuency:STARt {config.start_mhz}")
            if config.stop_mhz is not None and config.start_mhz is None:
                self._t.write(f"FREQuency:STOP {config.stop_mhz}")
            if config.reference_dbm is not None:
                self._t.write(f"AMPLitude:REFEerence {config.reference_dbm}")
            if config.attenuation is not None:
                self._t.write(f"AMPLitude:ATTenuation {config.attenuation}")

        run_with_retry(self.retry, _do, op="sa8.configure_scan")

    def capture_spectrum(self) -> Spectrum:
        def _do() -> Spectrum:
            if hasattr(self._t, "query_fixed"):
                payload = self._t.query_fixed("DATA:CURRent?", SA8_SWEEP_TOTAL_BYTES)  # type: ignore[attr-defined]
            else:
                payload = self._t.query_block("DATA:CURRent?")
            freq_hz, amplitude_dbm = decode_sa8_sweep_payload(payload)
            return Spectrum(
                freq_hz=freq_hz,
                amplitude_dbm=amplitude_dbm,
                timestamp=datetime.now(timezone.utc),
                trace="current",
                metadata=self._scan_metadata(),
            )

        return run_with_retry(self.retry, _do, op="sa8.capture_spectrum")

    def measure_peak(self, mode: PeakMode = PeakMode.AVR) -> Reading:
        def _do() -> Reading:
            # Device firmware accepts only the default query form (AVR).
            text = self._t.query("MEASure:PEAK?")
            value_dbm, freq_mhz = parse_peak_response(text)
            raw: dict[str, float | str] = {"requested_mode": mode.value}
            if freq_mhz is not None:
                raw["freq_mhz"] = freq_mhz
            return Reading(
                value=value_dbm,
                unit="dBm",
                quantity=QuantityKind.UNKNOWN,
                timestamp=datetime.now(timezone.utc),
                raw=raw,
            )

        return run_with_retry(self.retry, _do, op="sa8.measure_peak")

    def measure_floor(self) -> Reading:
        def _do() -> Reading:
            text = self._t.query("MEASure:FLOOr?")
            try:
                value = parse_floor_response(text)
            except ValueError:
                value = _parse_float(text)
            return Reading(
                value=value,
                unit="ADC",
                quantity=QuantityKind.UNKNOWN,
                timestamp=datetime.now(timezone.utc),
            )

        return run_with_retry(self.retry, _do, op="sa8.measure_floor")

    def query_center_mhz(self) -> float:
        return _parse_float(self._t.query("FREQuency:CENTer?"), "CENT")

    def query_span_mhz(self) -> float:
        return _parse_float(self._t.query("FREQuency:SPAN?"), "SPAN")

    def query_start_mhz(self) -> float:
        return _parse_float(self._t.query("FREQuency:STARt?"), "STAR")

    def query_stop_mhz(self) -> float:
        return _parse_float(self._t.query("FREQuency:STOP?"), "STOP")

    # --- RFSource ---

    def set_generator_enabled(self, enabled: bool) -> None:
        state = "ON" if enabled else "OFF"
        run_with_retry(self.retry, lambda: self._t.write(f"GENErator:STATus {state}"), op="sa8.gen_enable")

    def set_generator_frequency_mhz(self, freq_mhz: float) -> None:
        run_with_retry(self.retry, lambda: self._t.write(f"GENE:FREQ {freq_mhz}"), op="sa8.gen_freq")

    def set_generator_power_dbm(self, power_dbm: int) -> None:
        run_with_retry(self.retry, lambda: self._t.write(f"GENErator:ATTEnuation:DBM {power_dbm}"), op="sa8.gen_power")

    def set_generator_attenuator(self, att: int) -> None:
        run_with_retry(self.retry, lambda: self._t.write(f"GENErator:ATTEnuation:DIGI {att}"), op="sa8.gen_att")

    def query_generator_enabled(self) -> bool:
        text = self._t.query("GENErator:STATus?")
        val = parse_labeled_value(text, "STAT").upper()
        return val == "ON"

    def query_generator_frequency_mhz(self) -> float:
        return _parse_float(self._t.query("GENE:FREQ?"), "FREQ")

    # --- VectorAnalyzer ---

    def calibrate_sparam(
        self,
        param: SparamKind,
        standard: CalStandard,
        *,
        enabled: bool,
    ) -> None:
        state = "ON" if enabled else "OFF"
        if param == SparamKind.S11:
            cmd = f"DATA:SPARam:CALIbrate S11,{state}"
        else:
            cmd = f"DATA:SPARam:CALIbrate {param.value},{standard.value},{state}"
        run_with_retry(self.retry, lambda: self._t.write(cmd), op="sa8.sparam_cal")

    def capture_sparam_trace(self, param: SparamKind) -> Spectrum:
        def _do() -> Spectrum:
            start_hz = _mhz_to_hz(self.query_start_mhz())
            stop_hz = _mhz_to_hz(self.query_stop_mhz())
            if hasattr(self._t, "query_fixed"):
                # History trace is 302 int16 samples (604 bytes) when marker trace is active.
                payload = self._t.query_fixed("DATA:HISTory?", 604)  # type: ignore[attr-defined]
            else:
                payload = self._t.query_block("DATA:HISTory?")
            freq_hz, amplitude_dbm = decode_sa8_history_payload(payload, start_hz=start_hz, stop_hz=stop_hz)
            return Spectrum(
                freq_hz=freq_hz,
                amplitude_dbm=amplitude_dbm,
                timestamp=datetime.now(timezone.utc),
                trace=param.value.lower(),
                metadata={"param": param.value, **self._scan_metadata()},
            )

        return run_with_retry(self.retry, _do, op="sa8.capture_sparam")

    def set_marker_mode(
        self,
        enabled: bool,
        *,
        trace: str = "MAX",
        points: int = 8,
    ) -> None:
        state = "ON" if enabled else "OFF"
        run_with_retry(
            self.retry,
            lambda: self._t.write(f"MARK:MODE {state},{trace},{points}"),
            op="sa8.marker_mode",
        )

    def set_marker_frequency_mhz(self, index: int, freq_mhz: float) -> None:
        if index not in (1, 2, 3, 4):
            raise ValueError("marker index must be 1..4")
        run_with_retry(self.retry, lambda: self._t.write(f"MARK:MARK{index} {freq_mhz}"), op="sa8.marker_freq")

    def query_marker_dbm(self, index: int) -> float:
        if index not in (1, 2, 3, 4):
            raise ValueError("marker index must be 1..4")
        text = self._t.query(f"MARK:MARK{index}?")
        return _parse_float(text)

    def _scan_metadata(self) -> dict[str, float]:
        try:
            return {
                "center_mhz": self.query_center_mhz(),
                "span_mhz": self.query_span_mhz(),
                "start_mhz": self.query_start_mhz(),
                "stop_mhz": self.query_stop_mhz(),
            }
        except Exception:
            return {}
