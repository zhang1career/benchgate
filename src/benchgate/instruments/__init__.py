"""Unified instrument control: transports, capability protocols, drivers, registry.

Architecture: Transport (Bridge) -> Driver (Adapter) -> Capability (Protocol),
assembled via a Registry/Factory and bound to logical roles (scope/dmm/awg/sa/...).
"""

from __future__ import annotations

from .base import Instrument, run_with_retry
from .capabilities import (
    DigitalStimulus,
    Oscilloscope,
    PwmStimulus,
    RFSource,
    ROLE_CAPABILITY,
    ScalarReader,
    SpectrumAnalyzer,
    VectorAnalyzer,
)
from .errors import (
    CapabilityError,
    ConfigError,
    DecodeError,
    InstrumentConnectionError,
    InstrumentError,
    TimeoutInstrumentError,
    TransientInstrumentError,
)
from .registry import (
    DRIVER_REGISTRY,
    Bench,
    InstrumentConfig,
    ROLES,
    load_bench,
)
from .transport import ScpiChannel, SerialScpiTransport, SerialTransport, VisaTransport
from .types import (
    DEFAULT_RETRY,
    CalStandard,
    ChannelConfig,
    InstrumentInfo,
    PeakMode,
    QuantityKind,
    Reading,
    RetryPolicy,
    ScanConfig,
    ScalarSeries,
    Spectrum,
    SparamKind,
    TriggerConfig,
    TriggerSlope,
    Waveform,
)

__all__ = [
    "Instrument",
    "run_with_retry",
    "Oscilloscope",
    "ScalarReader",
    "DigitalStimulus",
    "SpectrumAnalyzer",
    "RFSource",
    "VectorAnalyzer",
    "PwmStimulus",
    "ROLE_CAPABILITY",
    "ScpiChannel",
    "VisaTransport",
    "SerialScpiTransport",
    "SerialTransport",
    "InstrumentError",
    "InstrumentConnectionError",
    "CapabilityError",
    "ConfigError",
    "TransientInstrumentError",
    "TimeoutInstrumentError",
    "DecodeError",
    "DRIVER_REGISTRY",
    "Bench",
    "InstrumentConfig",
    "ROLES",
    "load_bench",
    "DEFAULT_RETRY",
    "RetryPolicy",
    "InstrumentInfo",
    "Reading",
    "Waveform",
    "Spectrum",
    "ScalarSeries",
    "QuantityKind",
    "PeakMode",
    "SparamKind",
    "CalStandard",
    "ScanConfig",
    "TriggerConfig",
    "TriggerSlope",
    "ChannelConfig",
]
