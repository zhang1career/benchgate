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
  * ``mcu gpio write <pin> <0|1>`` -> ``mcu gpio write: pin=<name> val=<0|1>``.
"""

from __future__ import annotations

import time

from ..base import Instrument, run_with_retry
from ..errors import InstrumentError, TransientInstrumentError
from ..transport import SerialTransport
from ..types import InstrumentInfo, RetryPolicy

DRIVER_NAME = "tars_shell"
DEFAULT_PROMPT = "tars>"


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
        transport: SerialTransport | None = None,
    ) -> None:
        super().__init__(name, address, retry=retry)
        self.prompt = prompt
        self.assert_dtr = assert_dtr
        self.ready_timeout_s = ready_timeout_s
        self._t = transport or SerialTransport(
            address, baud=baud, bytesize=8, parity="N", stopbits=1, timeout_s=0.2
        )
        self._idn = ""

    @property
    def info(self) -> InstrumentInfo:
        return InstrumentInfo(
            driver=DRIVER_NAME,
            address=self._address,
            transport="serial",
            idn=self._idn or "TARS (stm32f429i-disc1)",
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
            self._idn = self._first_payload_line(resp) or ""
        except InstrumentError:
            self._idn = ""

    def disconnect(self) -> None:
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
    def _first_payload_line(resp: str) -> str:
        for line in resp.splitlines():
            s = line.strip()
            if s and s != DEFAULT_PROMPT and not s.endswith(">"):
                return s
        return ""

    # --- DigitalStimulus ---

    def set_level(self, channel: str, high: bool) -> None:
        resp = self._send(f"mcu gpio write {channel} {1 if high else 0}")
        if "unknown pin" in resp:
            raise InstrumentError(f"TARS: unknown pin {channel!r}")
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
