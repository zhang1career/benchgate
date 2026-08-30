"""TARS firmware driver — digital stimulus over the USB CDC shell.

TARS (STM32F429I-Discovery firmware) exposes an interactive text shell on its
USB CDC virtual serial port (``tars>`` prompt). This driver speaks that shell to
toggle whitelisted GPIO pins, exposing the ``DigitalStimulus`` capability so a
GPIO edge (0 -> 3.3 V) can act as the step source for RC characterisation.

Amplitude is fixed at the logic level and is not settable, so this is *not* a
``PwmStimulus``: general PWM (``mcu tim``) is still a firmware stub. PWM support
will be added here once the firmware wires it up.

Wire protocol (USB CDC):
  * 115200 8N1 by convention (baud ignored by USB CDC); DTR must be asserted.
  * Commands are ASCII + ``\\r\\n``; responses are text lines ending at ``tars>``.
  * ``mcu gpio write`` requires a resource grant: ``mcu res grant <pin> gpio``.
  * ``mcu gpio write <pin> <0|1>`` -> ``mcu gpio write: pin=<name> val=<0|1>``.
"""

from __future__ import annotations

import time

from pathlib import Path

from ..base import Instrument, run_with_retry
from ..errors import InstrumentConnectionError, InstrumentError, TransientInstrumentError
from ..transport import SerialShellTransport, SerialTransport
from ..types import InstrumentInfo, RetryPolicy

DRIVER_NAME = "tars_shell"
DEFAULT_PROMPT = "tars>"
_USB_PRODUCT = "TARS Virtual COM Port"


def _is_serial_path(text: str) -> bool:
    return text.startswith("/dev/") or text.upper().startswith("COM") or text.startswith("tty")


def _tars_cdc_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except ImportError as exc:  # pragma: no cover
        raise InstrumentConnectionError(
            "pyserial is required; install with: pip install benchgate[lab]"
        ) from exc
    found: list[str] = []
    for port in list_ports.comports():
        product = port.product or ""
        description = port.description or ""
        if _USB_PRODUCT.casefold() in product.casefold() or _USB_PRODUCT.casefold() in description.casefold():
            found.append(port.device)
    return found


def resolve_tars_address(address: str) -> str:
    """Resolve a serial path or USB product name (``TARS``) to a device path.

    A concrete ``/dev/cu.*`` path is used when it still exists. After a replug
    the CDC suffix changes; a missing path falls back to the USB product string
    ``TARS Virtual COM Port``.
    """
    text = address.strip()
    if not text:
        raise InstrumentConnectionError("TARS address is empty")
    if _is_serial_path(text) and Path(text).exists():
        return text

    wants_product = text.casefold() in {"tars", _USB_PRODUCT.casefold()}
    if not _is_serial_path(text) and not wants_product:
        raise InstrumentConnectionError(
            f"Unrecognized TARS address {address!r}. Use a cu.*/ttyACM* path or "
            f"{_USB_PRODUCT!r}."
        )

    matches = _tars_cdc_ports()
    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        raise InstrumentConnectionError(
            f"Multiple TARS CDC ports match: {unique}. Set a concrete serial path in instruments.yaml."
        )
    if _is_serial_path(text):
        raise InstrumentConnectionError(
            f"TARS serial path {text!r} is not present and no port with USB product "
            f"{_USB_PRODUCT!r} was found."
        )
    raise InstrumentConnectionError(
        f"No serial port with USB product {_USB_PRODUCT!r} matching {address!r}. "
        "Plug in TARS or set address to the cu.*/ttyACM* path."
    )


class TarsStimulus(Instrument):
    def __init__(
        self,
        name: str,
        address: str,
        *,
        baud: int = 115200,
        prompt: str = DEFAULT_PROMPT,
        assert_dtr: bool = True,
        ready_timeout_s: float = 3.0,
        retry: RetryPolicy | None = None,
        transport: SerialTransport | SerialShellTransport | None = None,
    ) -> None:
        resolved = address if transport is not None else resolve_tars_address(address)
        super().__init__(name, resolved, retry=retry)
        self._requested_address = address
        self.prompt = prompt
        self.assert_dtr = assert_dtr
        self.ready_timeout_s = ready_timeout_s
        self._t = transport or SerialShellTransport(
            resolved, baud=baud, timeout_s=0.2, assert_dtr=False
        )
        self._idn = ""
        self._gpio_granted: set[str] = set()

    @property
    def info(self) -> InstrumentInfo:
        meta: dict[str, str] = {"usb_product": _USB_PRODUCT}
        if self._requested_address != self._address:
            meta["requested_address"] = self._requested_address
        return InstrumentInfo(
            driver=DRIVER_NAME,
            address=self._address,
            transport="serial_shell",
            idn=self._idn or "TARS (stm32f429i-disc1)",
            metadata=meta,
        )

    def connect(self) -> None:
        if not self._t.is_open:
            self._t.open()
        if self.assert_dtr:
            # Mirror tars-send.py: drop then assert DTR to signal CDC ready.
            try:
                self._t.set_dtr(False)
                self._t.set_rts(False)
                time.sleep(0.05)
                self._t.set_dtr(True)
            except Exception:
                pass
            time.sleep(0.5)
        self._wait_ready()
        try:
            resp = self._send("mcu info")
            self._idn = self._mcu_info_line(resp) or self._first_payload_line(resp, skip="mcu info")
        except InstrumentError:
            self._idn = ""

    def disconnect(self) -> None:
        try:
            for pin in list(self._gpio_granted):
                self._send(f"mcu res grant {pin} none")
            self._gpio_granted.clear()
        except Exception:
            pass
        self._t.close()

    def _wait_ready(self) -> None:
        text = self._t.read_until_text(self.prompt, deadline_s=self.ready_timeout_s)
        if self.prompt in text or "shell ready" in text.lower():
            return
        # Nudge with a newline and retry once.
        self._t.write("\r\n")
        text = self._t.read_until_text(self.prompt, deadline_s=self.ready_timeout_s)
        if self.prompt not in text and "shell ready" not in text.lower():
            raise TransientInstrumentError("TARS shell prompt not seen")

    def _send(self, cmd: str, *, deadline_s: float = 2.0) -> str:
        def _do() -> str:
            self._t.flush_input()
            self._t.write(cmd + "\r\n")
            text = self._t.read_until_text(self.prompt, deadline_s=deadline_s)
            if self.prompt not in text:
                raise TransientInstrumentError(f"no prompt after {cmd!r}")
            return text

        return run_with_retry(self.retry, _do, op=f"tars.send:{cmd}")

    @staticmethod
    def _mcu_info_line(resp: str) -> str:
        for line in resp.splitlines():
            s = line.strip()
            if s.startswith("mcu:"):
                return s
        return ""

    @staticmethod
    def _first_payload_line(resp: str, skip: str = "") -> str:
        skip_cf = skip.casefold()
        for line in resp.splitlines():
            s = line.strip()
            if not s or s == DEFAULT_PROMPT or s.endswith(">"):
                continue
            if skip_cf and s.casefold() == skip_cf:
                continue
            return s
        return ""

    # --- DigitalStimulus ---

    @staticmethod
    def _parse_res_field(resp: str, key: str) -> str | None:
        prefix = f"{key}="
        for token in resp.replace("\r", " ").split():
            if token.startswith(prefix):
                return token.split("=", 1)[1]
        return None

    def _ensure_gpio_tenant(self, channel: str) -> None:
        """Current firmware refuses ``gpio write`` until the pin tenant is ``gpio``."""
        pin = channel.strip().casefold()
        if pin in self._gpio_granted:
            return
        status = self._send(f"mcu res status {channel}")
        if "res:" not in status:
            return
        tenant = self._parse_res_field(status, "tenant")
        if tenant == "gpio":
            return
        if tenant not in {None, "none", ""}:
            raise InstrumentError(
                f"TARS: pin {channel!r} is granted to tenant={tenant!r}; not stealing it"
            )
        grant = self._send(f"mcu res grant {channel} gpio")
        if self._parse_res_field(grant, "tenant") != "gpio" and "tenant=gpio" not in grant:
            raise InstrumentError(f"TARS: could not grant gpio on {channel}: {grant.strip()!r}")
        self._gpio_granted.add(pin)

    def set_level(self, channel: str, high: bool) -> None:
        self._ensure_gpio_tenant(channel)
        resp = self._send(f"mcu gpio write {channel} {1 if high else 0}")
        if "unknown pin" in resp:
            raise InstrumentError(f"TARS: unknown pin {channel!r}")
        if "err=" in resp:
            raise InstrumentError(f"TARS: gpio write failed: {resp.strip()!r}")
        if "val=" not in resp:
            raise InstrumentError(f"TARS: unexpected gpio response: {resp.strip()!r}")

    def read_level(self, channel: str) -> int:
        resp = self._send(f"mcu gpio read {channel}")
        if "unknown pin" in resp:
            raise InstrumentError(f"TARS: unknown pin {channel!r}")
        for token in resp.split():
            if token.startswith("val="):
                return int(token.split("=", 1)[1])
        raise InstrumentError(f"TARS: could not parse gpio read: {resp.strip()!r}")

    def step_edge(self, channel: str, *, rising: bool = True) -> None:
        """Drive ``channel`` to the post-edge level.

        Set the idle level via ``set_level`` and arm the scope *before* calling
        this so the capture sees a single clean transition.
        """
        self.set_level(channel, high=rising)
