"""Unified instrument control: transports, capability protocols, drivers, registry.

Architecture: Transport (Bridge) -> Driver (Adapter) -> Capability (Protocol),
assembled via a Registry/Factory and bound to logical roles (scope/dmm/awg).
"""

from __future__ import annotations

from .base import Instrument, run_with_retry
from .capabilities import (
    DigitalStimulus,
    Oscilloscope,
    PwmStimulus,
    ROLE_CAPABILITY,
    ScalarReader,
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
from .types import (
    DEFAULT_RETRY,
    ChannelConfig,
    InstrumentInfo,
    QuantityKind,
    Reading,
    RetryPolicy,
    ScalarSeries,
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
    "PwmStimulus",
    "ROLE_CAPABILITY",
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
    "ScalarSeries",
    "QuantityKind",
    "TriggerConfig",
    "TriggerSlope",
    "ChannelConfig",
]
