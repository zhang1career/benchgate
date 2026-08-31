"""Shared, driver-agnostic data types for instrument control."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import numpy as np


class QuantityKind(str, Enum):
    VOLTAGE = "voltage"
    CURRENT = "current"
    RESISTANCE = "resistance"
    CAPACITANCE = "capacitance"
    FREQUENCY = "frequency"
    PERCENT = "percent"
    TEMPERATURE = "temperature"
    RPM = "rpm"
    COUNT = "count"
    UNKNOWN = "unknown"


class TriggerSlope(str, Enum):
    RISING = "rising"
    FALLING = "falling"


class PeakMode(str, Enum):
    AVR = "AVR"
    MIN = "MIN"
    MID = "MID"
    RMS = "RMS"


class SparamKind(str, Enum):
    S11 = "S11"
    S21 = "S21"
    SWR = "SWR"


class CalStandard(str, Enum):
    SHORT = "SHORT"
    OPEN = "OPEN"
    LOAD = "LOAD"


@dataclass(frozen=True)
class RetryPolicy:
    """Uniform retry behaviour for any instrument operation.

    Applied identically to scopes, DMMs and stimulus devices: a transient I/O
    failure is retried up to ``attempts`` times with linear backoff; once the
    budget is exhausted the last error propagates (always an ``InstrumentError``).
    """

    attempts: int = 3
    backoff_s: float = 0.05

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("RetryPolicy.attempts must be >= 1")


DEFAULT_RETRY = RetryPolicy()


@dataclass(frozen=True)
class InstrumentInfo:
    """Identity and connection metadata — the single source of provenance."""

    driver: str
    address: str
    transport: str
    idn: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def as_provenance(self, role: str | None = None) -> dict[str, str]:
        """Flat dict suitable for embedding in MeasuredParams.instrument."""
        out = {
            "driver": self.driver,
            "address": self.address,
            "transport": self.transport,
            "idn": self.idn,
        }
        if role:
            out = {f"{role}_{k}": v for k, v in out.items()}
        return out


@dataclass(frozen=True)
class Reading:
    """A single scalar measurement (DMM, power monitor, ...).

    A returned ``Reading`` always means I/O succeeded. Device-side semantic
    states (overload, hold, ...) are conveyed via ``flags`` and may set
    ``value`` to ``inf``/``nan`` rather than raising.
    """

    value: float
    unit: str
    quantity: QuantityKind
    timestamp: datetime
    normalized_value: float | None = None
    normalized_unit: str | None = None
    flags: dict[str, bool] = field(default_factory=dict)
    raw: dict[str, Any] | None = None

    @property
    def overload(self) -> bool:
        return bool(self.flags.get("ovl") or self.flags.get("ul"))


@dataclass(frozen=True)
class Waveform:
    """A captured waveform on one channel.

    ``time_s`` is relative to the trigger instant. ``t0_utc`` anchors that
    instant to wall-clock time so multiple instruments can be aligned later.
    """

    time_s: np.ndarray
    voltage_v: np.ndarray
    channel: int
    timestamp: datetime
    sample_rate_hz: float | None = None
    raw_adc: np.ndarray | None = None
    scaling: dict[str, float] | None = None

    def __len__(self) -> int:
        return int(self.time_s.shape[0])


@dataclass(frozen=True)
class ScalarSeries:
    """A time series of scalar samples (e.g. polled DMM readings).

    ``t_rel_s`` is relative to the first sample. Parallel arrays keep the
    on-disk CSV layout trivial.
    """

    t_rel_s: np.ndarray
    values: np.ndarray
    unit: str
    quantity: QuantityKind
    t0_utc: datetime
    flags: list[dict[str, bool]] = field(default_factory=list)

    def __len__(self) -> int:
        return int(self.values.shape[0])


@dataclass(frozen=True)
class Spectrum:
    """A captured frequency-domain trace (spectrum analyzer, VNA magnitude, ...)."""

    freq_hz: np.ndarray
    amplitude_dbm: np.ndarray
    timestamp: datetime
    trace: str = "current"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.freq_hz.shape[0])


@dataclass
class ScanConfig:
    center_mhz: float | None = None
    span_mhz: float | None = None
    start_mhz: float | None = None
    stop_mhz: float | None = None
    reference_dbm: float | None = None
    attenuation: int | None = None


@dataclass
class TriggerConfig:
    source_channel: int = 1
    slope: TriggerSlope = TriggerSlope.RISING
    level_v: float = 0.0


@dataclass
class ChannelConfig:
    channel: int = 1
    enabled: bool = True


FRAME_UNITS = frozenset({"count", "degC"})


def calibration_is_complete(calibration: dict[str, Any] | None) -> bool:
    """``degC`` is only auditable when slope and offset are present."""
    if not calibration:
        return False
    kind = calibration.get("kind")
    if kind in (None, "none"):
        return False
    return calibration.get("slope") is not None and calibration.get("offset") is not None


@dataclass(frozen=True)
class Frame2D:
    """A 2-D scalar field on a regular grid (thermal array, scan map, …)."""

    values: np.ndarray
    unit: str
    quantity: QuantityKind
    timestamp: datetime
    mask: np.ndarray | None = None
    calibration: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        arr = np.asarray(self.values)
        if arr.ndim != 2:
            raise ValueError(f"Frame2D.values must be 2-D, got shape {arr.shape}")
        if self.unit not in FRAME_UNITS:
            raise ValueError(f"Frame2D.unit must be one of {sorted(FRAME_UNITS)}, got {self.unit!r}")
        if self.unit == "degC" and not calibration_is_complete(self.calibration):
            raise ValueError("Frame2D.unit='degC' requires calibration slope and offset")
        if self.mask is not None and tuple(self.mask.shape) != tuple(arr.shape):
            raise ValueError("Frame2D.mask shape must match values")

    @property
    def height(self) -> int:
        return int(self.values.shape[0])

    @property
    def width(self) -> int:
        return int(self.values.shape[1])


@dataclass(frozen=True)
class Frame2DSeries:
    """A burst of frames sharing one grid."""

    t_rel_s: np.ndarray
    values: np.ndarray
    unit: str
    quantity: QuantityKind
    t0_utc: datetime
    mask: np.ndarray | None = None
    calibration: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        arr = np.asarray(self.values)
        if arr.ndim != 3:
            raise ValueError(f"Frame2DSeries.values must be (n, h, w), got shape {arr.shape}")
        if int(np.asarray(self.t_rel_s).shape[0]) != arr.shape[0]:
            raise ValueError("Frame2DSeries.t_rel_s length must match values.shape[0]")
        if self.unit not in FRAME_UNITS:
            raise ValueError(f"Frame2DSeries.unit must be one of {sorted(FRAME_UNITS)}, got {self.unit!r}")
        if self.unit == "degC" and not calibration_is_complete(self.calibration):
            raise ValueError("Frame2DSeries.unit='degC' requires calibration slope and offset")
        if self.mask is not None and tuple(self.mask.shape) != tuple(arr.shape[1:]):
            raise ValueError("Frame2DSeries.mask shape must match (h, w)")

    def __len__(self) -> int:
        return int(self.values.shape[0])

    @property
    def height(self) -> int:
        return int(self.values.shape[1])

    @property
    def width(self) -> int:
        return int(self.values.shape[2])

    def frame(self, index: int = 0) -> Frame2D:
        t_rel = float(np.asarray(self.t_rel_s)[index])
        return Frame2D(
            values=self.values[index],
            unit=self.unit,
            quantity=self.quantity,
            timestamp=self.t0_utc + timedelta(seconds=t_rel),
            mask=self.mask,
            calibration=self.calibration,
            metadata=dict(self.metadata),
        )
