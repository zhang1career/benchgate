"""Shared data models for mapping, lab capture, and simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SpiceModelKind(str, Enum):
    PASSIVE = "passive"
    SUBCKT = "subckt"
    TABLE = "table"
    BUILTIN = "builtin"
    UNMAPPED = "unmapped"


@dataclass
class MeasuredParams:
    component_ref: str
    mpn: str
    captured_at: str
    session_id: str
    instrument: dict[str, str] = field(default_factory=dict)
    params: dict[str, float] = field(default_factory=dict)


@dataclass
class ComponentMapping:
    """KiCad symbol key → ngspice / Sim.* binding."""

    kicad_key: str
    reference: str | None = None
    spice_kind: SpiceModelKind = SpiceModelKind.UNMAPPED
    sim_library: Path | None = None
    sim_name: str | None = None
    sim_pins: str | None = None
    measured: MeasuredParams | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        if self.spice_kind == SpiceModelKind.UNMAPPED:
            return False
        if self.spice_kind in (SpiceModelKind.SUBCKT, SpiceModelKind.TABLE):
            return self.sim_library is not None and self.sim_library.exists()
        return self.spice_kind in (SpiceModelKind.PASSIVE, SpiceModelKind.BUILTIN)

    @property
    def status(self) -> str:
        if self.is_ready:
            return "ready"
        if self.spice_kind == SpiceModelKind.UNMAPPED:
            return "unmapped"
        return "pending"


@dataclass
class MappingManifest:
    version: int = 1
    entries: list[ComponentMapping] = field(default_factory=list)

    def find(self, kicad_key: str) -> ComponentMapping | None:
        for entry in self.entries:
            if entry.kicad_key == kicad_key:
                return entry
        return None

    def find_by_reference(self, reference: str) -> ComponentMapping | None:
        for entry in self.entries:
            if entry.reference == reference:
                return entry
        return None

    def upsert(self, mapping: ComponentMapping) -> None:
        for i, entry in enumerate(self.entries):
            if entry.kicad_key == mapping.kicad_key:
                self.entries[i] = mapping
                return
        self.entries.append(mapping)


def kicad_key(lib_id: str, value: str) -> str:
    return f"{lib_id}::{value}" if value else lib_id
