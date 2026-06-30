"""Instrument error hierarchy.

Two axes matter for callers:

* ``TransientInstrumentError`` and its subclasses are *retryable* I/O hiccups
  (bad serial frame, timeout). The retry helper consumes these and only the
  final one escapes after the policy is exhausted.
* Everything else is a hard error that should not be retried.

Semantic device states (overload, no-trigger, hold) are NOT errors: drivers
return a valid object with flags set instead of raising.
"""

from __future__ import annotations


class InstrumentError(Exception):
    """Base class for all instrument failures."""


class InstrumentConnectionError(InstrumentError):
    """Open/close or link-layer failure."""


class CapabilityError(InstrumentError):
    """Requested capability is not supported by the resolved instrument."""


class ConfigError(InstrumentError):
    """Invalid or missing instrument / role configuration."""


class TransientInstrumentError(InstrumentError):
    """Retryable I/O failure (consumed by the retry policy)."""


class TimeoutInstrumentError(TransientInstrumentError):
    """Read/query timed out."""


class DecodeError(TransientInstrumentError):
    """A frame was received but could not be parsed (likely a partial read)."""
