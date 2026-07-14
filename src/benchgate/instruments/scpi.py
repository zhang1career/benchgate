"""SCPI helpers: IEEE-488.2 arbitrary blocks and HTOOL-SA8 payload decoders."""

from __future__ import annotations

import struct
from typing import Callable

import numpy as np

from .errors import DecodeError

SA8_SWEEP_POINTS = 302
SA8_SWEEP_HEADER_BYTES = 6
SA8_POINT_BYTES = 6


def parse_labeled_value(text: str, *labels: str) -> str:
    """Extract the value from responses like ``CENT:100.5`` or ``CENT：100.5``."""
    text = text.strip().strip('"').strip("'")
    for label in labels:
        for sep in (":", "："):
            prefix = f"{label}{sep}"
            if text.upper().startswith(prefix.upper()):
                return text[len(prefix) :].strip().strip('"').strip("'")
    return text


def parse_arbitrary_block(buf: bytes, *, offset: int = 0) -> tuple[bytes, int]:
    """Parse a definite-length arbitrary block starting at ``offset``.

    Returns ``(payload, end_offset)`` where ``end_offset`` points past any
    trailing ``\\r\\n`` terminator.
    """
    if offset >= len(buf) or buf[offset : offset + 1] != b"#":
        raise DecodeError(f"expected arbitrary block at offset {offset}, got {buf[offset : offset + 8]!r}")
    pos = offset + 1
    if pos >= len(buf):
        raise DecodeError("truncated arbitrary block header")
    try:
        n_digits = int(chr(buf[pos]))
    except ValueError as exc:
        raise DecodeError(f"invalid arbitrary block digit count: {buf[pos]!r}") from exc
    pos += 1
    if pos + n_digits > len(buf):
        raise DecodeError("truncated arbitrary block length field")
    try:
        length = int(buf[pos : pos + n_digits].decode("ascii"))
    except ValueError as exc:
        raise DecodeError(f"invalid arbitrary block length: {buf[pos : pos + n_digits]!r}") from exc
    pos += n_digits
    end = pos + length
    if end > len(buf):
        raise DecodeError(f"arbitrary block payload truncated: need {length} bytes, have {len(buf) - pos}")
    payload = buf[pos:end]
    pos = end
    if pos + 2 <= len(buf) and buf[pos : pos + 2] == b"\r\n":
        pos += 2
    return payload, pos


def read_arbitrary_block(read: Callable[[int], bytes], *, max_header: int = 32) -> bytes:
    """Read one arbitrary block from a byte-oriented transport."""
    header = read(1)
    if header != b"#":
        raise DecodeError(f"expected '#', got {header!r}")
    n_digits_b = read(1)
    try:
        n_digits = int(n_digits_b.decode("ascii"))
    except ValueError as exc:
        raise DecodeError(f"invalid digit count {n_digits_b!r}") from exc
    if n_digits < 1 or n_digits > max_header:
        raise DecodeError(f"unreasonable arbitrary block length digits: {n_digits}")
    length_b = read(n_digits)
    try:
        length = int(length_b.decode("ascii"))
    except ValueError as exc:
        raise DecodeError(f"invalid length field {length_b!r}") from exc
    payload = read(length)
    if len(payload) != length:
        raise DecodeError(f"short read: expected {length} payload bytes, got {len(payload)}")
    term = read(2)
    if term and term != b"\r\n":
        # Some devices omit CRLF; tolerate a lone LF or no terminator.
        if term == b"\r":
            extra = read(1)
            if extra != b"\n":
                raise DecodeError(f"unexpected block terminator {term + extra!r}")
        elif term not in (b"\n", b""):
            raise DecodeError(f"unexpected block terminator {term!r}")
    return payload


def decode_sa8_sweep_payload(payload: bytes) -> tuple[np.ndarray, np.ndarray]:
    """Decode ``DATA:CURRent?`` sweep bytes into frequency (Hz) and amplitude (dBm)."""
    expected = SA8_SWEEP_HEADER_BYTES + SA8_SWEEP_POINTS * SA8_POINT_BYTES
    if len(payload) < expected:
        raise DecodeError(f"SA8 sweep payload too short: {len(payload)} < {expected}")
    body = payload[SA8_SWEEP_HEADER_BYTES : SA8_SWEEP_HEADER_BYTES + SA8_SWEEP_POINTS * SA8_POINT_BYTES]
    amps_dbm: list[float] = []
    freqs_hz: list[float] = []
    for i in range(SA8_SWEEP_POINTS):
        off = i * SA8_POINT_BYTES
        amp_raw, freq_khz = struct.unpack_from("<hi", body, off)
        amps_dbm.append(amp_raw * 0.01)
        freqs_hz.append(freq_khz * 1000.0)
    return np.asarray(freqs_hz, dtype=float), np.asarray(amps_dbm, dtype=float)


SA8_SWEEP_TOTAL_BYTES = 1820


def parse_peak_response(text: str) -> tuple[float, float | None]:
    """Parse ``Peak:-37.96,4480.070`` style responses."""
    val = parse_labeled_value(text, "Peak")
    if "," in val:
        dbm_s, freq_s = val.split(",", 1)
        return float(dbm_s), float(freq_s)
    return float(val), None


def parse_floor_response(text: str) -> float:
    """Parse ``Floor:632.480`` style responses."""
    return float(parse_labeled_value(text, "Floor"))


def decode_sa8_history_payload(payload: bytes, *, start_hz: float, stop_hz: float) -> tuple[np.ndarray, np.ndarray]:
    """Decode ``DATA:HISTory?`` int16 trace (0.01 dBm) onto a linear frequency axis."""
    count = len(payload) // 2
    if count < 1:
        raise DecodeError("SA8 history payload is empty")
    raw = np.frombuffer(payload[: count * 2], dtype="<i2")
    amps_dbm = raw.astype(float) * 0.01
    freqs_hz = np.linspace(start_hz, stop_hz, num=count, dtype=float)
    return freqs_hz, amps_dbm
