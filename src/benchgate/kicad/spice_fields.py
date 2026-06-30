"""Read/write KiCad Sim.* symbol properties."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kicad_tools import Schematic
from kicad_tools.schema.symbol import SymbolInstance, SymbolProperty


SIM_FIELDS = ("Sim.Library", "Sim.Name", "Sim.Pins", "Sim.Device", "Sim.Type")


@dataclass
class SimFields:
    library: str = ""
    name: str = ""
    pins: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.library and self.name)


def read_sim_fields(sym: SymbolInstance) -> SimFields:
    def _get(name: str) -> str:
        prop = sym.properties.get(name)
        return prop.value if prop else ""

    return SimFields(library=_get("Sim.Library"), name=_get("Sim.Name"), pins=_get("Sim.Pins"))


def write_sim_fields(
    sym: SymbolInstance,
    *,
    library: str,
    name: str,
    pins: str = "",
) -> None:
    sym.properties["Sim.Library"] = SymbolProperty("Sim.Library", library, visible=False)
    sym.properties["Sim.Name"] = SymbolProperty("Sim.Name", name, visible=False)
    if pins:
        sym.properties["Sim.Pins"] = SymbolProperty("Sim.Pins", pins, visible=False)


def apply_model_to_reference(
    schematic_path: Path,
    reference: str,
    *,
    sim_library: Path,
    sim_name: str,
    sim_pins: str = "",
) -> None:
    sch = Schematic.load(schematic_path)
    sym = sch.symbols.by_reference(reference)
    if sym is None:
        raise KeyError(f"Reference {reference!r} not found in {schematic_path}")
    write_sim_fields(
        sym,
        library=str(sim_library),
        name=sim_name,
        pins=sim_pins,
    )
    sch.save(schematic_path)
