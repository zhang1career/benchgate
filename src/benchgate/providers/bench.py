"""Bench measurement → ngspice subckt model provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchgate.schemas import (
    ComponentMapping,
    MeasuredParams,
    ModelArtifact,
    ModelProvenance,
    ModelSource,
)


@dataclass
class BenchModelProvider:
    """Register an existing bench-fitted subckt ``.lib`` with BENCH provenance."""

    lib_path: Path
    sim_name: str
    sim_pins: str | None = None
    measured: MeasuredParams | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    valid_range: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None
    tool: str = "benchgate lab fit"

    source = ModelSource.BENCH

    def build(self, entry: ComponentMapping, *, workdir: Path) -> ModelArtifact:
        path = self.lib_path.resolve()
        if not path.exists():
            raise FileNotFoundError(f"bench subckt not found: {path}")

        metrics = dict(self.metrics)
        if self.measured and self.measured.params:
            metrics.update(self.measured.params)

        provenance = ModelProvenance(
            source=ModelSource.BENCH,
            generated_at=self.measured.captured_at if self.measured else datetime.now(timezone.utc).isoformat(),
            tool=self.tool,
            source_files=[str(path)],
            valid_range=dict(self.valid_range or {}),
            metrics=metrics,
            notes=self.notes,
            measured=self.measured,
        )
        return ModelArtifact(
            lib_path=path,
            sim_name=self.sim_name,
            sim_pins=self.sim_pins,
            provenance=provenance,
        )
