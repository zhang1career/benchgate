"""Driver registry, configuration loading, and 3-layer role resolution.

Configuration precedence (highest wins):

    runtime overrides  >  project lab.yaml  >  global instruments.yaml

* ``instruments.yaml`` (``~/.benchgate/config/``) declares physical instruments
  and the default role bindings.
* ``<design>/models/lab.yaml`` optionally rebinds roles per project and carries
  capture defaults.
* CLI/Agent ``--instrument`` / ``--role`` parameters override both.

A *role* (scope/dmm/awg/sa/rfgen/vna/thermal) is a logical use mapped to one instrument; a verb in
the CLI selects an instrument by capability, never by device type guesswork.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from .base import Instrument
from .capabilities import ROLE_CAPABILITY
from .errors import CapabilityError, ConfigError
from .drivers.htool_sa8 import HtoolSA8
from .drivers.rigol_ds1104 import DS1104Scope
from .drivers.tars_shell import TarsStimulus
from .drivers.tinysa import TinySA
from .drivers.umeko_dec_h import UmekoDecH
from .drivers.uni_t_ut61e import UT61EDmm

_LOG = logging.getLogger(__name__)

DRIVER_REGISTRY: dict[str, type[Instrument]] = {
    "rigol_ds1104z": DS1104Scope,
    "uni_t_ut61e": UT61EDmm,
    "tars_shell": TarsStimulus,
    "htool_sa8": HtoolSA8,
    "tinysa": TinySA,
    "umeko_dec_h": UmekoDecH,
}


def register_driver(name: str, cls: type[Instrument]) -> None:
    """Register or replace a driver (in-tree or out-of-tree)."""
    DRIVER_REGISTRY[name] = cls

ROLES = tuple(ROLE_CAPABILITY.keys())


@dataclass
class InstrumentConfig:
    name: str
    driver: str
    address: str
    transport: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Bench:
    """Resolved view of instruments + role bindings for a design."""

    instruments: dict[str, InstrumentConfig]
    roles: dict[str, str | None]
    defaults: dict[str, Any] = field(default_factory=dict)
    capture: dict[str, Any] = field(default_factory=dict)

    def instrument_for_role(self, role: str) -> str | None:
        if role not in ROLE_CAPABILITY:
            raise ConfigError(f"Unknown role {role!r}; valid: {sorted(ROLE_CAPABILITY)}")
        return self.roles.get(role)

    def create(self, name: str) -> Instrument:
        if name not in self.instruments:
            raise ConfigError(f"Unknown instrument {name!r}; known: {sorted(self.instruments)}")
        cfg = self.instruments[name]
        driver_cls = DRIVER_REGISTRY.get(cfg.driver)
        if driver_cls is None:
            raise ConfigError(f"Unknown driver {cfg.driver!r}; known: {sorted(DRIVER_REGISTRY)}")
        options = dict(cfg.options)
        # Inject global VISA backend default for VISA drivers if unspecified.
        if cfg.transport == "visa" and "visa_backend" not in options and "visa_backend" in self.defaults:
            options["visa_backend"] = self.defaults["visa_backend"]
        try:
            return driver_cls(cfg.name, cfg.address, **options)
        except TypeError as exc:
            raise ConfigError(f"Bad options for {name!r} ({cfg.driver}): {exc}") from exc

    def open_role(self, role: str) -> Instrument:
        name = self.instrument_for_role(role)
        if not name:
            raise ConfigError(f"Role {role!r} is not bound to any instrument")
        return self.open_instrument(name, required_role=role)

    def open_instrument(self, name: str, *, required_role: str | None = None) -> Instrument:
        inst = self.create(name)
        if required_role is not None:
            capability = ROLE_CAPABILITY[required_role]
            if not isinstance(inst, capability):
                raise CapabilityError(
                    f"Instrument {name!r} ({self.instruments[name].driver}) "
                    f"does not satisfy role {required_role!r} ({capability.__name__})"
                )
        inst.connect()
        return inst

    def select(self, *, role: str | None = None, instrument: str | None = None) -> Instrument:
        """Resolve a single instrument for a CLI/Agent verb.

        Explicit ``instrument`` wins; capability is checked against ``role`` when
        both are supplied so e.g. ``read --instrument <scope>`` fails clearly.
        """
        if instrument:
            return self.open_instrument(instrument, required_role=role)
        if role:
            return self.open_role(role)
        raise ConfigError("Specify a role or an instrument")

    def capabilities(self, name: str) -> set[str]:
        """Roles whose protocol this instrument implements (no I/O)."""
        if name not in self.instruments:
            raise ConfigError(f"Unknown instrument {name!r}; known: {sorted(self.instruments)}")
        cfg = self.instruments[name]
        driver_cls = DRIVER_REGISTRY.get(cfg.driver)
        if driver_cls is None:
            return set()
        # Probe with a dummy path so USB-product addresses (tinySA) do not require hardware.
        try:
            inst = driver_cls(cfg.name, "/dev/null")
        except TypeError:
            inst = self.create(name)
        return {role for role, proto in ROLE_CAPABILITY.items() if isinstance(inst, proto)}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping")
    return data


def _parse_instruments(raw: dict[str, Any]) -> dict[str, InstrumentConfig]:
    out: dict[str, InstrumentConfig] = {}
    for name, spec in (raw.get("instruments") or {}).items():
        if not isinstance(spec, dict):
            raise ConfigError(f"instrument {name!r} must be a mapping")
        if "driver" not in spec or "address" not in spec:
            raise ConfigError(f"instrument {name!r} requires 'driver' and 'address'")
        out[name] = InstrumentConfig(
            name=name,
            driver=str(spec["driver"]),
            address=str(spec["address"]),
            transport=str(spec.get("transport", "")),
            options=dict(spec.get("options") or {}),
        )
    return out


def _known_roles(raw: Mapping[str, Any] | None, *, source: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for key, value in (raw or {}).items():
        if key in ROLE_CAPABILITY:
            out[key] = value
        else:
            _LOG.warning(
                "Unknown role %r in %s; valid: %s",
                key,
                source,
                sorted(ROLE_CAPABILITY),
            )
    return out


def _env_key(name: str) -> str:
    return re.sub(r"[^0-9A-Z]", "_", name.upper())


def _apply_env_overrides(
    instruments: dict[str, InstrumentConfig],
    roles: dict[str, str | None],
    env: Mapping[str, str],
) -> None:
    """Override instrument addresses from the environment (machine-specific).

    Two forms, per-instrument taking precedence over per-role:

      BENCHGATE_INSTRUMENT_<NAME>_ADDRESS   # by instrument logical name
      BENCHGATE_<ROLE>_ADDRESS              # by role -> its bound instrument

    Only addresses are overridden here; role *bindings* come from config / CLI.
    """
    # Role-based first (less specific).
    for role in ROLE_CAPABILITY:
        val = env.get(f"BENCHGATE_{role.upper()}_ADDRESS")
        bound = roles.get(role)
        if val and bound and bound in instruments:
            instruments[bound].address = val
    # Per-instrument name (more specific) wins.
    for name, cfg in instruments.items():
        val = env.get(f"BENCHGATE_INSTRUMENT_{_env_key(name)}_ADDRESS")
        if val:
            cfg.address = val


def load_bench(
    instruments_path: Path,
    *,
    project_lab_path: Path | None = None,
    overrides: dict[str, str | None] | None = None,
    env: Mapping[str, str] | None = None,
) -> Bench:
    """Build a :class:`Bench` by merging the configuration layers.

    Precedence (highest wins): runtime ``overrides`` > project ``lab.yaml`` >
    global ``instruments.yaml``. Environment variables additionally override
    instrument *addresses* (orthogonal to role bindings).
    """
    global_raw = _load_yaml(instruments_path)
    instruments = _parse_instruments(global_raw)
    defaults = dict(global_raw.get("defaults") or {})

    roles: dict[str, str | None] = {r: None for r in ROLE_CAPABILITY}
    roles.update(_known_roles(global_raw.get("roles"), source=str(instruments_path)))

    capture: dict[str, Any] = {}
    if project_lab_path is not None:
        project_raw = _load_yaml(project_lab_path)
        # A project may declare extra local instruments too.
        instruments.update(_parse_instruments(project_raw))
        roles.update(_known_roles(project_raw.get("roles"), source=str(project_lab_path)))
        capture = dict(project_raw.get("capture") or {})

    if overrides:
        roles.update(_known_roles(overrides, source="runtime overrides"))

    _apply_env_overrides(instruments, roles, os.environ if env is None else env)

    return Bench(instruments=instruments, roles=roles, defaults=defaults, capture=capture)
