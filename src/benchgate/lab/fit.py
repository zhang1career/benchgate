"""Generate ngspice subcircuits from measured parameters."""

from __future__ import annotations

from pathlib import Path


def rc_subckt_from_tau(name: str, r_ohm: float, tau_s: float, c_f: float | None = None) -> str:
    """Emit simple RC subcircuit; if c_f omitted, derive from tau/R."""
    c = c_f if c_f is not None else tau_s / r_ohm
    return f""".subckt {name} in out ref
R{name} in out {r_ohm}
C{name} out ref {c}
.ends {name}
"""


def write_subckt(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def measured_to_subckt(params: dict[str, float], name: str, r_ohm: float = 1e3) -> str:
    tau = params.get("tau_s", 1e-3)
    return rc_subckt_from_tau(name.upper(), r_ohm, tau)
