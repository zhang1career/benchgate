"""Model providers: turn a local (non-bench) source into an ngspice subckt artifact.

The global simulation engine is always ngspice. A ModelProvider produces a
portable ``.lib`` subckt (+ provenance) for one component/block; the global
netlist references it via ``.include`` + ``X``. There is no runtime co-simulation
coupling — the boundary is the subckt (pins + valid_range + provenance).

See docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md.
"""

from __future__ import annotations

from benchgate.providers.base import ModelProvider, register_model
from benchgate.providers.ltspice import (
    LtspiceModelProvider,
    netlist_to_subckt,
    normalize_ltspice_netlist,
)

__all__ = [
    "ModelProvider",
    "register_model",
    "LtspiceModelProvider",
    "netlist_to_subckt",
    "normalize_ltspice_netlist",
]
