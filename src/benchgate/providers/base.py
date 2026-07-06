"""ModelProvider protocol + manifest registration helper."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from benchgate.schemas import (
    ComponentMapping,
    MappingManifest,
    ModelArtifact,
    ModelSource,
    SpiceModelKind,
)


@runtime_checkable
class ModelProvider(Protocol):
    """Produces an ngspice-ready subckt artifact for a component/block."""

    source: ModelSource

    def build(self, entry: ComponentMapping, *, workdir: Path) -> ModelArtifact: ...


def register_model(
    manifest: MappingManifest,
    entry: ComponentMapping,
    artifact: ModelArtifact,
) -> ComponentMapping:
    """Bind an artifact onto a manifest entry and upsert.

    ``sim_library`` is stored as produced (absolute under global models);
    ``io.manifest`` relativizes it against the global models base on save.
    """
    entry.spice_kind = SpiceModelKind.SUBCKT
    entry.sim_library = artifact.lib_path
    entry.sim_name = artifact.sim_name
    entry.sim_pins = artifact.sim_pins
    entry.provenance = artifact.provenance
    manifest.upsert(entry)
    return entry
