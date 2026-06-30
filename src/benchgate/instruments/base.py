"""Instrument base class and the uniform retry helper."""

from __future__ import annotations

import abc
import time
from typing import Callable, TypeVar

from .errors import TransientInstrumentError
from .types import DEFAULT_RETRY, InstrumentInfo, RetryPolicy

T = TypeVar("T")


def run_with_retry(
    policy: RetryPolicy,
    fn: Callable[[], T],
    *,
    op: str = "operation",
) -> T:
    """Run ``fn`` retrying only on ``TransientInstrumentError``.

    Applied identically to every device. On exhaustion the last transient error
    propagates unchanged (it is already an ``InstrumentError``). Non-transient
    errors are never retried.
    """
    last: TransientInstrumentError | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return fn()
        except TransientInstrumentError as exc:
            last = exc
            if attempt < policy.attempts:
                time.sleep(policy.backoff_s * attempt)
    assert last is not None
    raise last


class Instrument(abc.ABC):
    """Lifecycle + identity contract shared by all drivers.

    Capabilities (Oscilloscope, ScalarReader, DigitalStimulus, ...) are
    structural Protocols implemented on top of this base, so a single driver can
    expose exactly the capabilities its hardware supports.
    """

    def __init__(self, name: str, address: str, *, retry: RetryPolicy | None = None) -> None:
        self.name = name
        self._address = address
        self.retry = retry or DEFAULT_RETRY

    @property
    @abc.abstractmethod
    def info(self) -> InstrumentInfo:
        ...

    @abc.abstractmethod
    def connect(self) -> None:
        ...

    @abc.abstractmethod
    def disconnect(self) -> None:
        ...

    def identify(self) -> str:
        return self.info.idn

    def __enter__(self) -> "Instrument":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()
