"""UT372 decoder and driver tests (no hardware; HID transport is faked)."""

from __future__ import annotations

import pytest

from benchgate.instruments.capabilities import ROLE_CAPABILITY
from benchgate.instruments.drivers.uni_t_ut372 import (
    PACKET_LEN,
    UT372Decoder,
    UT372Tach,
    encode_hex_pair,
)
from benchgate.instruments.errors import DecodeError, InstrumentConnectionError, TimeoutInstrumentError
from benchgate.instruments.hid_ch9325 import (
    Ch9325HidTransport,
    ch9325_feature_report,
    parse_ch9325_address,
    unwrap_ch9325_report,
)
from benchgate.instruments.types import QuantityKind, RetryPolicy


def _packet(*, digits: list[int], flags1: int = 0, flags2: int = 0x01, time_digits: list[int] | None = None) -> bytes:
    """Build a 27-byte UT372 frame. ``digits`` are LSD-first 7-seg bytes (DP in bit 7)."""
    if time_digits is None:
        time_digits = [0x7B] * 5
    body = b"0"
    for seg in digits:
        body += encode_hex_pair(seg)
    for seg in time_digits:
        body += encode_hex_pair(seg)
    body += encode_hex_pair(flags1) + encode_hex_pair(flags2) + b"\r\n"
    assert len(body) == PACKET_LEN
    return body


# 123.45 rpm: LSD-first 5,4,3|DP,2,1
RPM_123_45 = _packet(digits=[0x3D, 0x65, 0x7C | 0x80, 0x5E, 0x60], flags2=0x01)


def test_decode_hex_pair_matches_sigrok_lookup():
    assert encode_hex_pair(0x7B) == b"7;"
    assert encode_hex_pair(0x60) == b"60"
    assert encode_hex_pair(0x5E) == b"5>"


def test_decoder_rpm_with_decimal():
    res = UT372Decoder().decode(RPM_123_45)
    assert res["mode"] == "rpm"
    assert res["units"] == "rpm"
    assert res["val"] == pytest.approx(123.45)
    assert res["quantity"] is QuantityKind.RPM
    assert res["rpm"] is True
    assert res["count"] is False
    assert res["ovl"] is False


def test_decoder_count_mode():
    frame = _packet(digits=[0x7B, 0x60, 0x7B, 0x7B, 0x7B], flags2=0x02)  # 10 counts
    res = UT372Decoder().decode(frame)
    assert res["mode"] == "count"
    assert res["val"] == pytest.approx(10.0)
    assert res["quantity"] is QuantityKind.COUNT


def test_decoder_hold_max_flags():
    frame = _packet(digits=[0x7B] * 5, flags1=0x04, flags2=0x01 | 0x10)
    res = UT372Decoder().decode(frame)
    assert res["hold"] is True
    assert res["max"] is True
    assert res["min"] is False


def test_decoder_hold_allows_blank_time_field():
    frame = _packet(digits=[0x7B] * 5, flags1=0x04, flags2=0x01, time_digits=[0x00] * 5)
    res = UT372Decoder().decode(frame)
    assert res["hold"] is True
    assert res["time_val"] == 0.0
    assert res["val"] == pytest.approx(0.0)


def test_decoder_overload_L():
    frame = _packet(digits=[0x0B, 0x7B, 0x7B, 0x7B, 0x7B], flags2=0x01)
    res = UT372Decoder().decode(frame)
    assert res["ovl"] is True
    assert res["val"] == float("inf")
    assert res["norm_val"] is None


def test_decoder_setup_menu_raises():
    frame = _packet(digits=[0x7B] * 5, flags2=0x00)
    with pytest.raises(DecodeError, match="setup menu"):
        UT372Decoder().decode(frame)


def test_decoder_rejects_wrong_length():
    with pytest.raises(DecodeError):
        UT372Decoder().decode(b"0" * 10)


def test_decoder_accepts_missing_leading_ignore_byte():
    # Live CH9325 capture was 26 bytes (leading ignore nibble dropped).
    frame = b"3?7;7<0000705>657;007886\r\n"
    assert len(frame) == 26
    with pytest.raises(DecodeError, match="expected 27"):
        UT372Decoder().decode(frame)
    res = UT372Decoder().decode(frame, allow_short=True)
    assert res["mode"] == "count"
    assert res["val"] == pytest.approx(306.0)
    assert res["led"] is True


def test_decoder_rejects_unknown_glyph():
    frame = _packet(digits=[0x11, 0x7B, 0x7B, 0x7B, 0x7B], flags2=0x02)
    with pytest.raises(DecodeError, match="unknown UT372 glyph"):
        UT372Decoder().decode(frame)


def test_decoder_rejects_empty_digit_field():
    frame = _packet(digits=[0x00] * 5, flags2=0x02)
    with pytest.raises(DecodeError, match="empty"):
        UT372Decoder().decode(frame)


def test_decoder_rejects_blank_below_significant_digit():
    # LSD-first: blank in the ones place, then a 1.
    frame = _packet(digits=[0x00, 0x60, 0x7B, 0x7B, 0x7B], flags2=0x02)
    with pytest.raises(DecodeError, match="blank digit"):
        UT372Decoder().decode(frame)


# Mid-stream HID capture (feature-programmed 2400 8N1): 17-byte tail + three
# 27-byte COUNT=306 frames + a truncated start. Used to lock the 0.0 false-positive.
LIVE_UART_STREAM = (
    b"005>65605>007886\r\n"
    b"03?7;7<00005>65605>007886\r\n"
    b"03?7;7<00005>65605>007886\r\n"
    b"03?7;7<00007<65605>007886\r\n"
    b"03?7;7<00007<65605>00"
)


def test_misaligned_live_windows_do_not_decode_as_zero():
    dec = UT372Decoder()
    false_ok: list[tuple[int, int, float]] = []
    good = 0
    for offset in range(len(LIVE_UART_STREAM)):
        for length, allow_short in ((27, False), (26, True)):
            chunk = LIVE_UART_STREAM[offset : offset + length]
            if len(chunk) != length:
                continue
            try:
                res = dec.decode(chunk, allow_short=allow_short)
            except DecodeError:
                continue
            if res["val"] == pytest.approx(306.0) and chunk.endswith(b"\r\n"):
                good += 1
            else:
                false_ok.append((offset, length, float(res["val"])))
    assert false_ok == []
    assert good >= 3


class _FakeHidSerial:
    def __init__(self, frame: bytes):
        self._frame = frame
        self.is_open = False

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def flush_input(self):
        pass

    def read_until(self, expected, *, max_bytes=4096, timeout_s=None):
        return self._frame


def test_ut372_read_returns_reading():
    tach = UT372Tach("tach0", "1a86:e008", transport=_FakeHidSerial(RPM_123_45))
    tach.connect()
    reading = tach.read()
    assert reading.value == pytest.approx(123.45)
    assert reading.unit == "rpm"
    assert reading.quantity is QuantityKind.RPM
    assert reading.flags["rpm"] is True
    assert reading.normalized_value == pytest.approx(123.45)
    tach.disconnect()
    tach.disconnect()


def test_ut372_timeout_when_meter_silent():
    class _Empty:
        is_open = False

        def open(self):
            self.is_open = True

        def close(self):
            self.is_open = False

        def flush_input(self):
            return None

        def read_until(self, expected, *, max_bytes=4096, timeout_s=None):
            return b""

    tach = UT372Tach(
        "tach0",
        "auto",
        transport=_Empty(),
        timeout_s=0.05,
        retry=RetryPolicy(attempts=2, backoff_s=0),
    )
    tach.connect()
    with pytest.raises(TimeoutInstrumentError, match="USB=1"):
        tach.read()


def test_ut372_retries_then_raises_on_garbage():
    short = b"xxxx\r\n"
    tach = UT372Tach(
        "tach0",
        "auto",
        transport=_FakeHidSerial(short),
        timeout_s=0.05,
        retry=RetryPolicy(attempts=3, backoff_s=0),
    )
    tach.connect()
    with pytest.raises(DecodeError):
        tach.read()


def test_ut372_implements_tach_role():
    inst = UT372Tach("probe", "/dev/null")
    roles = [role for role, proto in ROLE_CAPABILITY.items() if isinstance(inst, proto)]
    assert "tach" in roles
    inst.disconnect()


def test_unwrap_ch9325_empty_and_payload():
    assert unwrap_ch9325_report(b"\xf0" + b"\x00" * 7) == b""
    assert unwrap_ch9325_report(b"\xf3ABC\x00\x00\x00\x00") == b"ABC"
    assert unwrap_ch9325_report(b"\x00\xf2XY\x00\x00\x00\x00\x00") == b"XY"


def test_unwrap_rejects_bad_framing():
    with pytest.raises(Exception, match="framing"):
        unwrap_ch9325_report(b"\x00\x01\x02\x03\x04\x05\x06\x07")


def test_parse_ch9325_address():
    assert parse_ch9325_address("auto") == (None, None, None)
    assert parse_ch9325_address("ch9325") == (None, None, None)
    assert parse_ch9325_address("1a86:e008") == (0x1A86, 0xE008, None)
    assert parse_ch9325_address("DevSrvsID:123") == (None, None, b"DevSrvsID:123")
    with pytest.raises(Exception):
        parse_ch9325_address("/dev/cu.usbmodem1")
    with pytest.raises(Exception):
        parse_ch9325_address("ut372")


def test_feature_report_2400_8n():
    assert ch9325_feature_report(2400, 8) == bytes([0x00, 0x60, 0x09, 0x00, 0x00, 0x03])


class _FakeHidDev:
    def __init__(self, uart: bytes):
        self._uart = uart
        self._i = 0
        self.feature: bytes | None = None

    def send_feature_report(self, data: bytes) -> int:
        self.feature = data
        return len(data)

    def read(self, size: int, timeout_ms: int) -> bytes:
        if not self._uart:
            return b"\xf0" + b"\x00" * 7
        if self._i >= len(self._uart):
            self._i = 0
        n = min(7, len(self._uart) - self._i)
        chunk = self._uart[self._i : self._i + n]
        self._i += n
        return bytes([0xF0 | n]) + chunk + b"\x00" * (7 - n)

    def close(self) -> None:
        return None


def test_driver_skips_tail_on_live_stream():
    hid = _FakeHidDev(LIVE_UART_STREAM + LIVE_UART_STREAM[17:44])
    transport = Ch9325HidTransport("auto", device=hid, timeout_s=1.0)
    tach = UT372Tach("tach0", "auto", transport=transport, timeout_s=1.0)
    tach.connect()
    first = tach.read()
    second = tach.read()
    assert first.value == pytest.approx(306.0)
    assert second.value == pytest.approx(306.0)
    assert first.unit == "count"
    tach.disconnect()


def test_driver_syncs_when_crlf_straddles_27_byte_cap():
    # 17-byte tail + two full 27-byte live frames (CR LF at byte 27).
    tail = b"005>65605>007886\r\n"
    frame = b"03?7;7<00005>65605>007886\r\n"
    assert len(frame) == PACKET_LEN
    hid = _FakeHidDev(tail + frame + frame)
    transport = Ch9325HidTransport("auto", device=hid, timeout_s=1.0)
    tach = UT372Tach("tach0", "auto", transport=transport)
    tach.connect()
    a = tach.read()
    b = tach.read()
    assert a.value == pytest.approx(306.0)
    assert b.value == pytest.approx(306.0)
    assert a.quantity is QuantityKind.COUNT
    tach.disconnect()


def test_driver_resyncs_mid_frame():
    hid = _FakeHidDev(RPM_123_45)
    hid._i = 11
    transport = Ch9325HidTransport("auto", device=hid, timeout_s=1.0)
    tach = UT372Tach("tach0", "auto", transport=transport)
    tach.connect()
    reading = tach.read()
    assert reading.value == pytest.approx(123.45)
    tach.disconnect()


def test_transport_reassembles_uart_packet():
    hid = _FakeHidDev(RPM_123_45)
    transport = Ch9325HidTransport("auto", device=hid, timeout_s=1.0)
    transport.open()
    assert hid.feature == ch9325_feature_report(2400, 8)
    hid._i = 0
    transport._buf.clear()
    frame = transport.read_until(b"\r\n", max_bytes=27)
    assert frame == RPM_123_45
    transport.close()


def test_keepalives_do_not_reconnect():
    class _Keepalive(_FakeHidDev):
        def read(self, size: int, timeout_ms: int) -> bytes:
            return b"\xf0" + b"\x00" * 7

    hid = _Keepalive(b"")
    transport = Ch9325HidTransport("auto", device=hid, timeout_s=0.2)
    transport.open()
    assert transport.read_until(b"\r\n", max_bytes=8) == b""
    transport.close()


def test_configure_tolerates_feature_report_failure():
    class _NoFeature(_FakeHidDev):
        def send_feature_report(self, data: bytes) -> int:
            raise InstrumentConnectionError("feature failed")

    transport = Ch9325HidTransport("auto", device=_NoFeature(RPM_123_45), timeout_s=1.0)
    transport.open()
    transport._buf.clear()
    frame = transport.read_until(b"\r\n", max_bytes=27)
    assert frame == RPM_123_45
    transport.close()
