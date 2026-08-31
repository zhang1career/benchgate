"""WCH CH9325 / Hoitek HE2325U serial-over-HID transport.

These chips present a vendor HID profile (VID:PID ``1a86:e008`` or
``04fa:2490``) and bridge a UART into 8-byte interrupt reports. Several UNI-T
instruments use them, including the UT372 tachometer.

RX HID report (always 8 bytes)::

    [0xf0 | n] [n payload bytes] [zero pad]     n <= 7

Feature report programs the UART. hidapi wants a leading report-ID byte
(``0x00``); the 5-byte payload (IOKit ``MaxFeatureReportSize``) is::

    baud_le16, 0x00, 0x00, (data_bits - 5)

The OS HID stack owns the interface, so this transport talks through hidapi
(not libusb). The shared library is loaded via ctypes; the optional ``hid``
Python package is used when already importable.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
import time
from ctypes import (
    POINTER,
    Structure,
    c_char_p,
    c_int,
    c_size_t,
    c_ushort,
    c_void_p,
    c_wchar_p,
)
from typing import Protocol

from .errors import InstrumentConnectionError, TransientInstrumentError

CH9325_VID = 0x1A86
CH9325_PID = 0xE008
HE2325U_VID = 0x04FA
HE2325U_PID = 0x2490
KNOWN_VID_PIDS: tuple[tuple[int, int], ...] = (
    (CH9325_VID, CH9325_PID),
    (HE2325U_VID, HE2325U_PID),
)

HID_REPORT_LEN = 8
MAX_PAYLOAD = 7
DEFAULT_BAUD = 2400
DEFAULT_DATA_BITS = 8
DUMMY_ADDRESSES = frozenset({"/dev/null", "COM0", "none"})


class HidDevice(Protocol):
    def send_feature_report(self, data: bytes) -> int: ...

    def read(self, size: int, timeout_ms: int) -> bytes: ...

    def close(self) -> None: ...


def unwrap_ch9325_report(data: bytes) -> bytes:
    """Extract UART bytes from one CH9325 HID input report.

    hidapi may prepend a zero report ID on numbered-report backends. Empty
    keep-alives (``0xf0``) yield ``b""``.
    """
    if not data:
        return b""
    raw = data
    if len(raw) == HID_REPORT_LEN + 1 and raw[0] == 0:
        raw = raw[1:]
    if (raw[0] & 0xF0) != 0xF0:
        raise TransientInstrumentError(f"CH9325 framing: expected 0xFn, got 0x{raw[0]:02x}")
    count = raw[0] & 0x0F
    if count > MAX_PAYLOAD:
        raise TransientInstrumentError(f"CH9325 payload length {count} exceeds {MAX_PAYLOAD}")
    return bytes(raw[1 : 1 + count])


def parse_ch9325_address(address: str) -> tuple[int | None, int | None, bytes | None]:
    """Parse ``auto`` / ``vid:pid`` / hid path. Dummy paths are rejected here."""
    text = address.strip()
    if not text or text.casefold() in {"auto", "ch9325"}:
        return None, None, None
    if text.startswith("DevSrvsID:") or text.startswith("/dev/hidraw"):
        return None, None, text.encode("ascii")
    if ":" in text and not text.startswith("/"):
        left, right = text.split(":", 1)
        if right.startswith(":"):
            raise InstrumentConnectionError(f"Bad CH9325 address {address!r}")
        try:
            vid = int(left, 16)
            pid = int(right, 16)
        except ValueError as exc:
            raise InstrumentConnectionError(f"Bad CH9325 address {address!r}") from exc
        if not (0 <= vid <= 0xFFFF and 0 <= pid <= 0xFFFF):
            raise InstrumentConnectionError(f"Bad CH9325 address {address!r}")
        return vid, pid, None
    raise InstrumentConnectionError(
        f"Unrecognized CH9325 address {address!r}; use 'auto', '1a86:e008', or a hid path"
    )


def ch9325_feature_report(baud: int, data_bits: int = DEFAULT_DATA_BITS) -> bytes:
    if baud < 1 or baud > 0xFFFF:
        raise ValueError(f"CH9325 baud out of range: {baud}")
    if not 5 <= data_bits <= 8:
        raise ValueError(f"CH9325 data_bits must be 5..8, got {data_bits}")
    return bytes([0x00, baud & 0xFF, (baud >> 8) & 0xFF, 0x00, 0x00, data_bits - 5])


def _library_candidates() -> list[str]:
    names = []
    found = ctypes.util.find_library("hidapi")
    if found:
        names.append(found)
    if sys.platform == "darwin":
        names.extend(
            [
                "/usr/local/lib/libhidapi.dylib",
                "/opt/homebrew/lib/libhidapi.dylib",
            ]
        )
    elif sys.platform.startswith("linux"):
        names.extend(["libhidapi-hidraw.so.0", "libhidapi-libusb.so.0", "libhidapi.so.0"])
    elif sys.platform == "win32":
        names.extend(["hidapi.dll", "libhidapi-0.dll"])
    return names


class _CtypesHidInfo(Structure):
    pass


_CtypesHidInfo._fields_ = [
    ("path", c_char_p),
    ("vendor_id", c_ushort),
    ("product_id", c_ushort),
    ("serial_number", c_wchar_p),
    ("release_number", c_ushort),
    ("manufacturer_string", c_wchar_p),
    ("product_string", c_wchar_p),
    ("usage_page", c_ushort),
    ("usage", c_ushort),
    ("interface_number", c_int),
    ("next", POINTER(_CtypesHidInfo)),
]


class _CtypesHidDevice:
    def __init__(self, lib: ctypes.CDLL, handle: c_void_p) -> None:
        self._lib = lib
        self._handle = handle

    def send_feature_report(self, data: bytes) -> int:
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        rc = self._lib.hid_send_feature_report(self._handle, buf, len(data))
        if rc < 0:
            raise InstrumentConnectionError(f"HID feature report failed: {self._error()}")
        return int(rc)

    def read(self, size: int, timeout_ms: int) -> bytes:
        buf = (ctypes.c_ubyte * size)()
        n = self._lib.hid_read_timeout(self._handle, buf, size, timeout_ms)
        if n == 0:
            return b""
        if n < 0:
            # Darwin hidapi sometimes returns -1 with "Success" on idle timeout.
            err = self._error()
            if not err or err.casefold() == "success":
                return b""
            raise TransientInstrumentError(f"HID read failed: {err}")
        return bytes(buf[:n])

    def close(self) -> None:
        if self._handle:
            self._lib.hid_close(self._handle)
            self._handle = None

    def _error(self) -> str:
        try:
            msg = self._lib.hid_error(self._handle)
        except Exception:
            return ""
        return str(msg or "")


class _CtypesHidApi:
    """Minimal hidapi binding (enumerate / open / feature / read)."""

    def __init__(self, exclusive: bool = True) -> None:
        self._lib = self._load()
        self._lib.hid_init.restype = c_int
        self._lib.hid_enumerate.argtypes = [c_ushort, c_ushort]
        self._lib.hid_enumerate.restype = POINTER(_CtypesHidInfo)
        self._lib.hid_free_enumeration.argtypes = [c_void_p]
        self._lib.hid_open_path.argtypes = [c_char_p]
        self._lib.hid_open_path.restype = c_void_p
        self._lib.hid_open.argtypes = [c_ushort, c_ushort, c_wchar_p]
        self._lib.hid_open.restype = c_void_p
        self._lib.hid_close.argtypes = [c_void_p]
        self._lib.hid_read_timeout.argtypes = [c_void_p, c_void_p, c_size_t, c_int]
        self._lib.hid_read_timeout.restype = c_int
        self._lib.hid_send_feature_report.argtypes = [c_void_p, c_void_p, c_size_t]
        self._lib.hid_send_feature_report.restype = c_int
        self._lib.hid_error.argtypes = [c_void_p]
        self._lib.hid_error.restype = c_wchar_p
        if self._lib.hid_init() != 0:
            raise InstrumentConnectionError("hid_init failed")
        setter = getattr(self._lib, "hid_darwin_set_open_exclusive", None)
        if setter is not None:
            setter.argtypes = [c_int]
            setter(1 if exclusive else 0)

    @staticmethod
    def _load() -> ctypes.CDLL:
        last: Exception | None = None
        for name in _library_candidates():
            try:
                return ctypes.CDLL(name)
            except OSError as exc:
                last = exc
        raise InstrumentConnectionError(
            "hidapi is required for CH9325 HID devices "
            "(brew install hidapi, or install libhidapi). "
            f"Last error: {last}"
        ) from last

    def enumerate(self, vid: int, pid: int) -> list[dict]:
        head = self._lib.hid_enumerate(vid, pid)
        out: list[dict] = []
        cur = head
        try:
            while cur:
                info = cur.contents
                path = bytes(info.path) if info.path else b""
                out.append(
                    {
                        "path": path,
                        "vendor_id": int(info.vendor_id),
                        "product_id": int(info.product_id),
                        "product_string": info.product_string or "",
                    }
                )
                cur = info.next
        finally:
            if head:
                self._lib.hid_free_enumeration(head)
        return out

    def open_path(self, path: bytes) -> _CtypesHidDevice:
        handle = self._lib.hid_open_path(path)
        if not handle:
            raise InstrumentConnectionError(f"HID open failed for {path!r}")
        return _CtypesHidDevice(self._lib, handle)

    def open_vid_pid(self, vid: int, pid: int) -> _CtypesHidDevice:
        handle = self._lib.hid_open(vid, pid, None)
        if not handle:
            raise InstrumentConnectionError(f"HID open failed for {vid:04x}:{pid:04x}")
        return _CtypesHidDevice(self._lib, handle)


class _PyHidDevice:
    def __init__(self, device: object) -> None:
        self._dev = device

    def send_feature_report(self, data: bytes) -> int:
        rc = self._dev.send_feature_report(list(data))  # type: ignore[attr-defined]
        if rc is None or int(rc) < 0:
            raise InstrumentConnectionError("HID feature report failed")
        return int(rc)

    def read(self, size: int, timeout_ms: int) -> bytes:
        raw = self._dev.read(size, timeout_ms=timeout_ms)  # type: ignore[attr-defined]
        if not raw:
            return b""
        return bytes(raw)

    def close(self) -> None:
        self._dev.close()  # type: ignore[attr-defined]


class _PyHidApi:
    def __init__(self, module: object) -> None:
        self._hid = module

    def enumerate(self, vid: int, pid: int) -> list[dict]:
        rows = self._hid.enumerate(vid, pid)  # type: ignore[attr-defined]
        out = []
        for row in rows:
            path = row.get("path") or b""
            if isinstance(path, str):
                path = path.encode("ascii")
            out.append(
                {
                    "path": path,
                    "vendor_id": int(row.get("vendor_id") or 0),
                    "product_id": int(row.get("product_id") or 0),
                    "product_string": row.get("product_string") or "",
                }
            )
        return out

    def open_path(self, path: bytes) -> _PyHidDevice:
        device = self._hid.device()  # type: ignore[attr-defined]
        device.open_path(path)
        return _PyHidDevice(device)

    def open_vid_pid(self, vid: int, pid: int) -> _PyHidDevice:
        device = self._hid.device()  # type: ignore[attr-defined]
        device.open(vid, pid)
        return _PyHidDevice(device)


_HID_BACKEND = None


def _open_hid_backend(exclusive: bool = True):
    """Process-wide hidapi handle.

    ``hid_darwin_set_open_exclusive`` is global to the library, so a second
    backend with a different ``exclusive`` flag would silently override the first.
    """
    global _HID_BACKEND
    if _HID_BACKEND is not None:
        return _HID_BACKEND
    try:
        import hid as hid_mod  # type: ignore[import-not-found]
    except ImportError:
        hid_mod = None
    if hid_mod is not None and hasattr(hid_mod, "device"):
        _HID_BACKEND = _PyHidApi(hid_mod)
    else:
        _HID_BACKEND = _CtypesHidApi(exclusive=exclusive)
    return _HID_BACKEND


class Ch9325HidTransport:
    """UART stream over a CH9325/HE2325U HID device.

    Surface matches the ``SerialTransport`` subset used by passive readers
    (``open`` / ``close`` / ``read`` / ``read_until`` / ``flush_input``).
    """

    def __init__(
        self,
        address: str,
        *,
        baud: int = DEFAULT_BAUD,
        data_bits: int = DEFAULT_DATA_BITS,
        timeout_s: float = 2.0,
        exclusive: bool = True,
        program_baud: bool = True,
        backend=None,
        device: HidDevice | None = None,
    ) -> None:
        self.address = address
        self.baud = baud
        self.data_bits = data_bits
        self.timeout_s = timeout_s
        self.exclusive = exclusive
        self.program_baud = program_baud
        self._backend = backend
        self._injected = device
        self._dev: HidDevice | None = None
        self._buf = bytearray()

    @property
    def is_open(self) -> bool:
        return self._dev is not None

    def open(self) -> None:
        if self._dev is not None:
            return
        if self._injected is not None:
            self._dev = self._injected
            self._configure()
            return
        if self.address.strip() in DUMMY_ADDRESSES:
            raise InstrumentConnectionError(f"Refusing to open dummy address {self.address!r}")
        self._backend = self._backend or _open_hid_backend(exclusive=self.exclusive)
        last: Exception | None = None
        for delay in (0.0, 0.6, 1.2, 2.0):
            if delay:
                time.sleep(delay)
            try:
                self._dev = self._open_device(self._backend)
                last = None
                break
            except InstrumentConnectionError as exc:
                last = exc
        if self._dev is None:
            raise last or InstrumentConnectionError("HID open failed")
        time.sleep(0.15)
        if self.program_baud:
            self._configure()

    def close(self) -> None:
        if self._dev is not None and self._injected is None:
            try:
                self._dev.close()
            except Exception:
                pass
        self._dev = None
        self._buf.clear()

    def flush_input(self) -> None:
        self._buf.clear()
        if self._dev is None:
            return
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            chunk = self._read_hid(20)
            if not chunk:
                break

    def read(self, size: int) -> bytes:
        self._fill(size)
        take = bytes(self._buf[:size])
        del self._buf[:size]
        return take

    def read_until(
        self,
        expected: bytes,
        *,
        max_bytes: int = 4096,
        timeout_s: float | None = None,
    ) -> bytes:
        if not expected:
            raise ValueError("expected must be non-empty")
        budget = self.timeout_s if timeout_s is None else timeout_s
        deadline = time.monotonic() + max(0.0, budget)
        while expected not in self._buf:
            if len(self._buf) >= max_bytes:
                break
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            chunk = self._read_hid(remaining_ms)
            if chunk:
                self._buf.extend(chunk)
            elif time.monotonic() >= deadline:
                break
            else:
                time.sleep(0.01)
        idx = self._buf.find(expected)
        if idx < 0:
            out = bytes(self._buf[:max_bytes])
            del self._buf[: len(out)]
            return out
        end = idx + len(expected)
        out = bytes(self._buf[:end])
        del self._buf[:end]
        return out

    def _configure(self) -> None:
        assert self._dev is not None
        report = ch9325_feature_report(self.baud, self.data_bits)
        try:
            self._dev.send_feature_report(report)
        except InstrumentConnectionError:
            # Do not reopen: extra exclusive seizes reset the CH9325 on macOS.
            pass
        time.sleep(0.05)

    def _open_device(self, backend) -> HidDevice:
        vid, pid, path = parse_ch9325_address(self.address)
        if path:
            return backend.open_path(path)
        if vid is not None and pid is not None and hasattr(backend, "open_vid_pid"):
            return backend.open_vid_pid(vid, pid)
        return backend.open_path(self._resolve_path(backend))

    def _resolve_path(self, backend) -> bytes:
        vid, pid, path = parse_ch9325_address(self.address)
        if path:
            return path
        pairs = ((vid, pid),) if vid is not None and pid is not None else KNOWN_VID_PIDS
        matches: list[bytes] = []
        for v, p in pairs:
            for row in backend.enumerate(v, p):
                if row["path"]:
                    matches.append(row["path"])
        if not matches:
            wanted = f"{vid:04x}:{pid:04x}" if vid is not None and pid is not None else "1a86:e008"
            raise InstrumentConnectionError(f"No CH9325 HID device found ({wanted})")
        if len(matches) > 1 and vid is None:
            raise InstrumentConnectionError(
                f"Multiple CH9325 devices; set address to vid:pid or a hid path ({len(matches)} found)"
            )
        return matches[0]

    def _fill(self, size: int) -> None:
        deadline = time.monotonic() + self.timeout_s
        while len(self._buf) < size and time.monotonic() < deadline:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            chunk = self._read_hid(remaining_ms)
            if chunk:
                self._buf.extend(chunk)
            else:
                time.sleep(0.005)

    def _read_hid(self, timeout_ms: int) -> bytes:
        if self._dev is None:
            raise InstrumentConnectionError("CH9325 HID device is not open")
        started = time.monotonic()
        raw = self._dev.read(HID_REPORT_LEN, timeout_ms)
        if not raw:
            # Immediate empty on Darwin is a dead or idle handle, not a timeout.
            if time.monotonic() - started < 0.03:
                time.sleep(0.02)
            return b""
        return unwrap_ch9325_report(raw)
