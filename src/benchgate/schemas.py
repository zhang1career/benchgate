"""Shared data models for mapping, lab capture, and simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SpiceModelKind(str, Enum):
    """How the model is represented in the ngspice netlist."""

    PASSIVE = "passive"
    SUBCKT = "subckt"
    TABLE = "table"
    BUILTIN = "builtin"
    UNMAPPED = "unmapped"


class ModelSource(str, Enum):
    """Where a subckt model came from (orthogonal to SpiceModelKind)."""

    BENCH = "bench"          # PyVISA bench measurement + fit
    LTSPICE = "ltspice"      # locally simulated in LTspice / .asc, exported
    DATASHEET = "datasheet"  # fitted from datasheet curves
    VENDOR = "vendor"        # vendor .lib referenced as-is (may be unverified)
    MANUAL = "manual"        # hand-written / manually bound


@dataclass
class MeasuredParams:
    component_ref: str
    mpn: str
    captured_at: str
    session_id: str
    instrument: dict[str, str] = field(default_factory=dict)
    params: dict[str, float] = field(default_factory=dict)


@dataclass
class ModelProvenance:
    """Provenance + validity contract for a subckt model artifact.

    The global↔local simulation boundary is the subckt: pins + valid_range +
    this provenance. There is no runtime co-simulation coupling.
    """

    source: ModelSource
    generated_at: str = ""
    tool: str | None = None
    source_files: list[str] = field(default_factory=list)
    checksum: str | None = None
    valid_range: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None
    measured: MeasuredParams | None = None  # bench detail when source == BENCH


@dataclass
class ModelArtifact:
    """Output of a ModelProvider: an ngspice-ready subckt + its provenance."""

    lib_path: Path
    sim_name: str
    sim_pins: str | None
    provenance: ModelProvenance


@dataclass
class ComponentMapping:
    """KiCad symbol key → ngspice / Sim.* binding."""

    kicad_key: str
    reference: str | None = None
    spice_kind: SpiceModelKind = SpiceModelKind.UNMAPPED
    sim_library: Path | None = None
    sim_name: str | None = None
    sim_pins: str | None = None
    provenance: ModelProvenance | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def measured(self) -> MeasuredParams | None:
        """Bench measurement detail, if this model came from the bench."""
        return self.provenance.measured if self.provenance else None

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


MANIFEST_VERSION = 2


@dataclass
class MappingManifest:
    version: int = MANIFEST_VERSION
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
