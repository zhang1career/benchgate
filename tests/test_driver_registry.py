"""Driver registry consistency and out-of-tree registration."""

from __future__ import annotations

import pytest

from benchgate.instruments.base import Instrument
from benchgate.instruments.capabilities import ROLE_CAPABILITY
from benchgate.instruments.registry import DRIVER_REGISTRY, register_driver
from benchgate.instruments.types import InstrumentInfo


@pytest.mark.parametrize("name", sorted(DRIVER_REGISTRY))
def test_every_builtin_driver_has_a_role(name):
    cls = DRIVER_REGISTRY[name]
    inst = cls("probe", "/dev/null")
    roles = [role for role, proto in ROLE_CAPABILITY.items() if isinstance(inst, proto)]
    assert roles, f"{name} implements no ROLE_CAPABILITY protocol"
    assert inst.retry is not None
    inst.disconnect()
    inst.disconnect()


def test_register_driver_and_reject_capabilityless_class():
    class Bare(Instrument):
        def __init__(self, name: str, address: str, **_kwargs) -> None:
            super().__init__(name, address)

        @property
        def info(self) -> InstrumentInfo:
            return InstrumentInfo(driver="bare", address=self._address, transport="none")

        def connect(self) -> None:
            return None

        def disconnect(self) -> None:
            return None

    register_driver("bare_probe", Bare)
    try:
        inst = Bare("x", "/dev/null")
        roles = [role for role, proto in ROLE_CAPABILITY.items() if isinstance(inst, proto)]
        assert roles == []
    finally:
        DRIVER_REGISTRY.pop("bare_probe", None)
