"""Transport layer (Bridge): isolates drivers from pyvisa / pyserial.

Transports:

* ``VisaTransport``        — message-based SCPI over pyvisa (oscilloscope).
* ``SerialScpiTransport``  — SCPI framing over pyserial (HTOOL-SA8 CDC-ACM).
* ``SerialTransport``      — raw serial; passive telemetry DMMs.
* ``SerialShellTransport`` — CDC text shell + optional binary frames (TARS, Umeko DEC-H).

Third-party libraries are imported lazily so the package imports without the
optional ``[lab]`` extra installed.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from .errors import (
    DecodeError,
    InstrumentConnectionError,
    TimeoutInstrumentError,
    TransientInstrumentError,
)


def _require(module: str, extra: str = "lab"):
    try:
        return __import__(module)
    except ImportError as exc:  # pragma: no cover - exercised only without extras
        raise InstrumentConnectionError(
            f"'{module}' is required; install with: pip install benchgate[{extra}]"
        ) from exc


@runtime_checkable
class ScpiChannel(Protocol):
    """SCPI command channel shared by VISA and serial-SCPI transports."""

    @property
    def is_open(self) -> bool:
        ...

    def open(self) -> None:
        ...

    def close(self) -> None:
        ...

    def write(self, cmd: str) -> None:
        ...

    def query(self, cmd: str) -> str:
        ...

    def query_block(self, cmd: str) -> bytes:
        ...


class VisaTransport:
    """Thin wrapper over a pyvisa MessageBasedResource."""

    def __init__(
        self,
        address: str,
        *,
        timeout_ms: int = 10_000,
        backend: str | None = None,
        read_termination: str = "\n",
        write_termination: str = "\n",
    ) -> None:
        self.address = address
        self.timeout_ms = timeout_ms
        # None -> ResourceManager() (NI-VISA / system backend). "@py" forces
        # pyvisa-py. DS1104Z over USB is validated only on the system backend.
        self.backend = backend
        self.read_termination = read_termination
        self.write_termination = write_termination
        self._rm: Any = None
        self._res: Any = None

    @property
    def is_open(self) -> bool:
        return self._res is not None

    def open(self) -> None:
        pyvisa = _require("pyvisa")
        try:
            self._rm = pyvisa.ResourceManager(self.backend) if self.backend else pyvisa.ResourceManager()
            self._res = self._rm.open_resource(self.address)
        except Exception as exc:
            raise InstrumentConnectionError(f"VISA open failed for {self.address}: {exc}") from exc
        self._res.timeout = self.timeout_ms
        self._res.read_termination = self.read_termination
        self._res.write_termination = self.write_termination

    def close(self) -> None:
        for dev in (self._res, self._rm):
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
        self._res = None
        self._rm = None

    def _wrap(self, exc: Exception) -> TransientInstrumentError:
        name = type(exc).__name__.lower()
        if "timeout" in name or "timeout" in str(exc).lower():
            return TimeoutInstrumentError(str(exc))
        return TransientInstrumentError(str(exc))

    def write(self, cmd: str) -> None:
        try:
            self._res.write(cmd)
        except Exception as exc:
            raise self._wrap(exc) from exc

    def query(self, cmd: str) -> str:
        try:
            return self._res.query(cmd)
        except Exception as exc:
            raise self._wrap(exc) from exc

    def query_binary_values(self, cmd: str, **kwargs: Any):
        try:
            return self._res.query_binary_values(cmd, **kwargs)
        except Exception as exc:
            raise self._wrap(exc) from exc

    def read_raw(self) -> bytes:
        try:
            return self._res.read_raw()
        except Exception as exc:
            raise self._wrap(exc) from exc

    def query_block(self, cmd: str) -> bytes:
        """Query returning an IEEE-488.2 definite-length arbitrary block."""
        from .scpi import read_arbitrary_block

        try:
            self._res.write(cmd)
            return read_arbitrary_block(lambda n: self._res.read_bytes(n))
        except Exception as exc:
            raise self._wrap(exc) from exc


class SerialScpiTransport:
    """SCPI over a USB CDC-ACM / RS-232 serial port (``\\r\\n`` framing)."""

    def __init__(
        self,
        port: str,
        *,
        baud: int = 115200,
        timeout_s: float = 2.0,
        read_termination: bytes = b"\r\n",
        write_termination: str = "\r\n",
    ) -> None:
        self.port = port
        self.baud = baud
        self.timeout_s = timeout_s
        self.read_termination = read_termination
        self.write_termination = write_termination
        self._ser = SerialTransport(port, baud=baud, timeout_s=timeout_s)

    @property
    def is_open(self) -> bool:
        return self._ser.is_open

    def open(self) -> None:
        self._ser.open()
        self.flush_input()
        time.sleep(0.05)

    def close(self) -> None:
        self._ser.close()

    def flush_input(self) -> None:
        self._ser.flush_input()

    def write(self, cmd: str) -> None:
        text = cmd.rstrip("\r\n") + self.write_termination
        try:
            self._ser.write(text)
        except Exception as exc:
            raise TransientInstrumentError(f"serial SCPI write failed: {exc}") from exc

    def query(self, cmd: str) -> str:
        from .errors import DecodeError

        self.write(cmd)
        try:
            raw = self._ser.read_until(self.read_termination)
        except Exception as exc:
            raise TransientInstrumentError(f"serial SCPI read failed: {exc}") from exc
        if not raw:
            raise TimeoutInstrumentError(f"no response to {cmd!r}")
        text = raw.decode("ascii", errors="replace").strip()
        if not text:
            raise DecodeError(f"empty response to {cmd!r}")
        return text

    def query_block(self, cmd: str) -> bytes:
        from .scpi import read_arbitrary_block

        self.write(cmd)
        try:
            return read_arbitrary_block(lambda n: self._ser.read(n))
        except Exception as exc:
            if isinstance(exc, TransientInstrumentError):
                raise
            raise TransientInstrumentError(f"serial SCPI block read failed: {exc}") from exc

    def query_fixed(self, cmd: str, nbytes: int) -> bytes:
        """Read an exact byte count (SA8 sweep payloads omit the ``#N`` block header)."""
        self.write(cmd)
        buf = bytearray()
        deadline = time.monotonic() + self.timeout_s
        while len(buf) < nbytes and time.monotonic() < deadline:
            chunk = self._ser.read(nbytes - len(buf))
            if chunk:
                buf.extend(chunk)
            else:
                time.sleep(0.01)
        if len(buf) < nbytes:
            raise TimeoutInstrumentError(f"short read for {cmd!r}: expected {nbytes}, got {len(buf)}")
        return bytes(buf)

    def prime_scpi(self, *, settle_s: float = 0.4) -> str:
        """Enter SCPI mode: ``*IDN?`` must be the first command after open."""
        from .errors import DecodeError

        self.flush_input()
        self.write("*IDN?")
        time.sleep(settle_s)
        try:
            raw = self._ser.read_until(self.read_termination)
        except Exception as exc:
            raise TransientInstrumentError(f"serial SCPI read failed during *IDN?: {exc}") from exc
        if not raw:
            raise TimeoutInstrumentError("no response to *IDN?")
        text = raw.decode("ascii", errors="replace").strip()
        if not text:
            raise DecodeError("empty response to *IDN?")
        return text


class SerialTransport:
    """Wrapper over pyserial. Friendly params translated lazily to constants."""

    def __init__(
        self,
        port: str,
        *,
        baud: int = 9600,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        timeout_s: float = 1.0,
        dtr: bool | None = None,
        rts: bool | None = None,
    ) -> None:
        self.port = port
        self.baud = baud
        self.bytesize = bytesize
        self.parity = parity.upper()
        self.stopbits = stopbits
        self.timeout_s = timeout_s
        self.dtr = dtr
        self.rts = rts
        self._ser: Any = None

    @property
    def is_open(self) -> bool:
        return self._ser is not None and getattr(self._ser, "is_open", False)

    def open(self) -> None:
        serial = _require("serial")
        bytesize_map = {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS, 8: serial.EIGHTBITS}
        parity_map = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
            "M": serial.PARITY_MARK,
            "S": serial.PARITY_SPACE,
        }
        stopbits_map = {
            1: serial.STOPBITS_ONE,
            1.5: serial.STOPBITS_ONE_POINT_FIVE,
            2: serial.STOPBITS_TWO,
        }
        try:
            self._ser = serial.Serial(
                self.port,
                self.baud,
                bytesize=bytesize_map[self.bytesize],
                parity=parity_map[self.parity],
                stopbits=stopbits_map[self.stopbits],
                timeout=self.timeout_s,
            )
            if self.dtr is not None:
                self._ser.dtr = self.dtr
            if self.rts is not None:
                self._ser.rts = self.rts
        except KeyError as exc:
            raise InstrumentConnectionError(f"Unsupported serial parameter: {exc}") from exc
        except Exception as exc:
            raise InstrumentConnectionError(f"Serial open failed for {self.port}: {exc}") from exc

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None

    def set_dtr(self, value: bool) -> None:
        self._ser.dtr = value

    def set_rts(self, value: bool) -> None:
        self._ser.rts = value

    def flush_input(self) -> None:
        try:
            self._ser.reset_input_buffer()
        except Exception:
            pass

    def write(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("ascii")
        try:
            self._ser.write(data)
        except Exception as exc:
            raise TransientInstrumentError(f"serial write failed: {exc}") from exc

    def read(self, size: int) -> bytes:
        try:
            return self._ser.read(size)
        except Exception as exc:
            raise TransientInstrumentError(f"serial read failed: {exc}") from exc

    def read_until(self, expected: bytes, *, max_bytes: int = 4096) -> bytes:
        try:
            return self._ser.read_until(expected, max_bytes)
        except Exception as exc:
            raise TransientInstrumentError(f"serial read_until failed: {exc}") from exc

    def read_line(self) -> bytes:
        return self.read_until(b"\n")

    def read_until_text(self, marker: str, *, deadline_s: float) -> str:
        """Accumulate decoded text until ``marker`` appears or the deadline passes."""
        end = time.monotonic() + deadline_s
        buf = b""
        while time.monotonic() < end:
            chunk = self.read(256)
            if chunk:
                buf += chunk
                if marker.encode("ascii") in buf:
                    break
            else:
                time.sleep(0.01)
        return buf.decode("ascii", errors="replace")


class SerialShellTransport:
    """CDC / UART text shell with optional fixed-size binary payloads.

    Wraps :class:`SerialTransport`. Drivers that already speak ``write`` /
    ``read_until_text`` (TARS) can use this as a drop-in; thermal / binary-frame
    drivers use :meth:`command`, :meth:`drain`, :meth:`read_exactly`, and
    :meth:`read_until_marker`.
    """

    def __init__(
        self,
        port: str,
        *,
        baud: int = 115200,
        timeout_s: float = 2.0,
        assert_dtr: bool = True,
        write_termination: str = "\r\n",
        inner: SerialTransport | None = None,
    ) -> None:
        self.port = port
        self.baud = baud
        self.timeout_s = timeout_s
        self.assert_dtr = assert_dtr
        self.write_termination = write_termination
        self._ser = inner or SerialTransport(
            port,
            baud=baud,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout_s=timeout_s,
            dtr=True if assert_dtr else None,
        )
        self._pushback = bytearray()

    @property
    def is_open(self) -> bool:
        return self._ser.is_open

    def open(self) -> None:
        self._ser.open()
        if self.assert_dtr:
            try:
                self._ser.set_dtr(True)
            except Exception:
                pass

    def close(self) -> None:
        self._ser.close()

    def set_dtr(self, value: bool) -> None:
        self._ser.set_dtr(value)

    def set_rts(self, value: bool) -> None:
        self._ser.set_rts(value)

    def flush_input(self) -> None:
        self._pushback.clear()
        self._ser.flush_input()

    def write(self, data: bytes | str) -> None:
        self._ser.write(data)

    def read(self, size: int) -> bytes:
        if self._pushback:
            take = bytes(self._pushback[:size])
            del self._pushback[:size]
            if len(take) < size:
                take += self._ser.read(size - len(take))
            return take
        return self._ser.read(size)

    def read_until(self, expected: bytes, *, max_bytes: int = 4096) -> bytes:
        return self._ser.read_until(expected, max_bytes=max_bytes)

    def read_line(self) -> bytes:
        return self._ser.read_line()

    def read_until_text(self, marker: str, *, deadline_s: float) -> str:
        return self._ser.read_until_text(marker, deadline_s=deadline_s)

    def drain(self, quiet_s: float = 0.3, *, max_bytes: int = 1_000_000) -> bytes:
        """Read until ``quiet_s`` elapses with no new bytes."""
        buf = bytearray()
        last = time.monotonic()
        deadline = time.monotonic() + max(self.timeout_s, quiet_s * 20)
        while time.monotonic() < deadline:
            chunk = self.read(4096)
            if chunk:
                buf.extend(chunk)
                last = time.monotonic()
                if len(buf) >= max_bytes:
                    break
            elif time.monotonic() - last >= quiet_s:
                break
            else:
                time.sleep(0.01)
        return bytes(buf)

    def command(self, cmd: str, *, quiet_s: float = 0.3) -> str:
        """Send a text command and return the drained response as text."""
        self.flush_input()
        term = self.write_termination
        self.write(cmd.rstrip("\r\n") + term)
        return self.drain(quiet_s).decode("ascii", errors="replace")

    def read_exactly(self, n: int, *, timeout_s: float) -> bytes:
        buf = bytearray()
        deadline = time.monotonic() + timeout_s
        while len(buf) < n and time.monotonic() < deadline:
            chunk = self.read(n - len(buf))
            if chunk:
                buf.extend(chunk)
            else:
                time.sleep(0.005)
        if len(buf) < n:
            raise TimeoutInstrumentError(f"short read: expected {n}, got {len(buf)}")
        return bytes(buf)

    def read_until_marker(self, marker: bytes, *, timeout_s: float, max_bytes: int = 1_000_000) -> bytes:
        """Accumulate bytes until ``marker`` appears (inclusive)."""
        if not marker:
            raise ValueError("marker must be non-empty")
        buf = bytearray()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            chunk = self.read(256)
            if chunk:
                buf.extend(chunk)
                idx = buf.find(marker)
                if idx >= 0:
                    self._pushback.extend(buf[idx + len(marker) :])
                    return bytes(buf[: idx + len(marker)])
                if len(buf) > max_bytes:
                    raise DecodeError(f"marker {marker!r} not found before max_bytes")
            else:
                time.sleep(0.005)
        raise TimeoutInstrumentError(f"marker {marker!r} not seen")
