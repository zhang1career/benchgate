"""Rigol DS1104Z oscilloscope driver (USB VISA, SCPI).

Implements ``Oscilloscope``. Ported from the standalone DS1104 capture script;
the inline SCPI sequence is wrapped behind the capability methods and runs under
the uniform retry policy. Only the DS1104Z is targeted (no DS1000 family base).

NOTE: USB capture is validated on the system VISA backend (NI-VISA);
``ResourceManager("@py")`` does not enumerate this scope reliably, so the VISA
backend defaults to the system backend and is overridable per instrument.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import numpy as np

from ..base import Instrument, run_with_retry
from ..transport import VisaTransport
from ..types import InstrumentInfo, RetryPolicy, TriggerConfig, TriggerSlope, Waveform

DRIVER_NAME = "rigol_ds1104z"


class DS1104Scope(Instrument):
    def __init__(
        self,
        name: str,
        address: str,
        *,
        timeout_ms: int = 10_000,
        visa_backend: str | None = None,
        retry: RetryPolicy | None = None,
        transport: VisaTransport | None = None,
    ) -> None:
        super().__init__(name, address, retry=retry)
        self._t = transport or VisaTransport(address, timeout_ms=timeout_ms, backend=visa_backend)
        self._idn = ""

    @property
    def info(self) -> InstrumentInfo:
        return InstrumentInfo(
            driver=DRIVER_NAME,
            address=self._address,
            transport="visa",
            idn=self._idn,
        )

    def connect(self) -> None:
        self._t.open()
        self._idn = run_with_retry(self.retry, lambda: self._t.query("*IDN?").strip(), op="ds1104.idn")

    def disconnect(self) -> None:
        self._t.close()

    def identify(self) -> str:
        return self._idn

    def auto_setup(self) -> None:
        self._t.write(":AUT")
        time.sleep(2.0)

    def configure_trigger(self, config: TriggerConfig) -> None:
        slope = "POS" if config.slope == TriggerSlope.RISING else "NEG"
        self._t.write(":TRIG:MODE EDGE")
        self._t.write(f":TRIG:EDGE:SOUR CHAN{config.source_channel}")
        self._t.write(f":TRIG:EDGE:SLOP {slope}")
        self._t.write(f":TRIG:EDGE:LEV {config.level_v}")

    def enable_channel(self, channel: int = 1, enabled: bool = True) -> None:
        self._t.write(f":CHAN{channel}:DISP {'ON' if enabled else 'OFF'}")

    def single_capture(self) -> None:
        self._t.write(":SING")

    def capture_waveform(self, channel: int = 1) -> Waveform:
        def _do() -> Waveform:
            ch = f"CHAN{channel}"
            self._t.write(f":WAV:SOUR {ch}")
            self._t.write(":WAV:FORM BYTE")
            self._t.write(":WAV:MODE NORM")
            data = self._t.query_binary_values(":WAV:DATA?", datatype="B", container=np.array)
            xinc = float(self._t.query(":WAV:XINC?"))
            xorg = float(self._t.query(":WAV:XOR?"))
            yinc = float(self._t.query(":WAV:YINC?"))
            yorg = float(self._t.query(":WAV:YOR?"))
            yref = float(self._t.query(":WAV:YREF?"))
            samples = np.asarray(data, dtype=float)
            voltage = (samples - yref) * yinc + yorg
            time_s = np.arange(len(voltage)) * xinc + xorg
            return Waveform(
                time_s=time_s,
                voltage_v=voltage,
                channel=channel,
                timestamp=datetime.now(timezone.utc),
                sample_rate_hz=(1.0 / xinc) if xinc else None,
                raw_adc=np.asarray(data),
                scaling={"xinc": xinc, "xorg": xorg, "yinc": yinc, "yorg": yorg, "yref": yref},
            )

        return run_with_retry(self.retry, _do, op="ds1104.capture")

    def screenshot_png(self) -> bytes:
        def _do() -> bytes:
            self._t.write(":DISP:DATA? ON,0,PNG")
            return self._t.read_raw()

        return run_with_retry(self.retry, _do, op="ds1104.screenshot")
