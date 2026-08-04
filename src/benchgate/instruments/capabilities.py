"""Capability protocols.

Capabilities are split by what hardware can actually do, rather than forcing a
deep inheritance tree. A driver implements the subset it supports:

* DS1104Z  -> Oscilloscope
* UT61E    -> ScalarReader (read-only telemetry; not configurable)
* TARS     -> DigitalStimulus (3.3 V logic levels; amplitude is not settable)
* HTOOL SA8 -> SpectrumAnalyzer + RFSource + VectorAnalyzer
* tinySA   -> SpectrumAnalyzer + RFSource (no VNA)

``PwmStimulus`` is reserved for when the TARS firmware wires up general PWM
(``mcu tim`` is currently a stub); no driver implements it yet.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import (
    CalStandard,
    PeakMode,
    Reading,
    ScanConfig,
    Spectrum,
    SparamKind,
    TriggerConfig,
    Waveform,
)


@runtime_checkable
class ScalarReader(Protocol):
    """Reads a single scalar quantity (DMM, power/temperature monitor)."""

    def read(self) -> Reading:
        ...


@runtime_checkable
class Oscilloscope(Protocol):
    """Captures time-domain waveforms."""

    def auto_setup(self) -> None:
        ...

    def configure_trigger(self, config: TriggerConfig) -> None:
        ...

    def single_capture(self) -> None:
        ...

    def capture_waveform(self, channel: int = 1) -> Waveform:
        ...

    def screenshot_png(self) -> bytes:
        ...


@runtime_checkable
class DigitalStimulus(Protocol):
    """Drives digital logic levels (fixed amplitude). Usable as a step source."""

    def set_level(self, channel: str, high: bool) -> None:
        ...

    def step_edge(self, channel: str, *, rising: bool = True) -> None:
        ...


@runtime_checkable
class SpectrumAnalyzer(Protocol):
    """Captures frequency-domain spectra and scalar RF measurements."""

    def configure_scan(self, config: ScanConfig) -> None:
        ...

    def capture_spectrum(self) -> Spectrum:
        ...

    def measure_peak(self, mode: PeakMode = PeakMode.AVR) -> Reading:
        ...

    def measure_floor(self) -> Reading:
        ...


@runtime_checkable
class RFSource(Protocol):
    """Built-in tracking / signal generator."""

    def set_generator_enabled(self, enabled: bool) -> None:
        ...

    def set_generator_frequency_mhz(self, freq_mhz: float) -> None:
        ...

    def set_generator_power_dbm(self, power_dbm: int) -> None:
        ...

    def set_generator_attenuator(self, att: int) -> None:
        ...


@runtime_checkable
class VectorAnalyzer(Protocol):
    """Scalar-network / reflection measurements (S11, S21, SWR)."""

    def calibrate_sparam(
        self,
        param: SparamKind,
        standard: CalStandard,
        *,
        enabled: bool,
    ) -> None:
        ...

    def capture_sparam_trace(self, param: SparamKind) -> Spectrum:
        ...


@runtime_checkable
class PwmStimulus(Protocol):
    """Reserved: arbitrary PWM. Not implemented by any driver yet."""

    def set_pwm(self, channel: str, *, frequency_hz: float, duty: float) -> None:
        ...

    def stop_pwm(self, channel: str) -> None:
        ...


# Logical role -> required capability. Used by the registry to validate bindings.
ROLE_CAPABILITY: dict[str, type] = {
    "scope": Oscilloscope,
    "dmm": ScalarReader,
    "awg": DigitalStimulus,
    "sa": SpectrumAnalyzer,
    "rfgen": RFSource,
    "vna": VectorAnalyzer,
}
