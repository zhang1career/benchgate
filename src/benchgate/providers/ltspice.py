"""LTspice → ngspice model provider.

Input: an LTspice-exported SPICE netlist (``.net`` / ``.cir``), representing a
sub-block to characterize offline. (Direct ``.asc`` ingestion needs an LTspice
install + Wine on macOS — see docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md §5.1 — so it
is out of the default headless path.)

Output: a normalized ngspice ``.subckt`` ``.lib`` + provenance.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchgate.lab.fit import write_subckt
from benchgate.schemas import ComponentMapping, ModelArtifact, ModelProvenance, ModelSource

# LTspice analysis / GUI directives that must not survive into a reusable subckt.
_STRIP_DIRECTIVES = frozenset(
    {
        "tran", "ac", "dc", "op", "noise", "tf", "four", "fft",
        "step", "meas", "measure", "save", "probe", "plot", "print",
        "backanno", "wave", "loadbias", "savebias", "end",
    }
)
# Directives kept verbatim inside the subckt body.
_KEEP_DIRECTIVES = frozenset(
    {"model", "param", "func", "include", "lib", "global", "ic", "nodeset", "temp"}
)
# Standard element prefixes (drop V/I stimulus in flat-wrap mode).
_SOURCE_PREFIXES = ("V", "I")


def _logical_lines(text: str) -> list[str]:
    """Join SPICE ``+`` continuation lines into single logical lines."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if stripped.startswith("+"):
            cont = stripped[1:].strip()
            if out:
                out[-1] = out[-1] + " " + cont
            else:
                out.append(cont)
        else:
            out.append(line)
    return out


def _looks_like_ltspice_stdlib(line: str) -> bool:
    low = line.lower()
    return any(tok in low for tok in ("ltspice", "\\lib\\", "/lib/", "standard.", "cmp/"))


def normalize_ltspice_netlist(text: str) -> tuple[list[str], list[str]]:
    """Return (logical_lines, warnings) with LTspice-only syntax normalized.

    - micro sign (µ/μ) → ``u``
    - drops analysis/GUI directives (``.tran``/``.step``/``.meas``/…)
    - warns on LTspice standard-library ``.lib``/``.include`` (ngspice can't read them)
    """
    warnings: list[str] = []
    text = text.replace("\u00b5", "u").replace("\u03bc", "u")
    lines: list[str] = []
    for line in _logical_lines(text):
        stripped = line.strip()
        if not stripped or stripped.startswith("*"):
            lines.append(line)
            continue
        if stripped.startswith("."):
            directive = re.split(r"[\s]", stripped[1:], maxsplit=1)[0].lower()
            if directive in _STRIP_DIRECTIVES:
                warnings.append(f"dropped LTspice directive: {stripped.splitlines()[0][:60]}")
                continue
            if directive in ("lib", "include") and _looks_like_ltspice_stdlib(stripped):
                warnings.append(
                    f"references an LTspice standard library ngspice cannot read: {stripped[:60]}"
                )
        lines.append(line)
    return lines, warnings


def _is_element(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith(("*", ".")) and s[0].isalpha()


def _extract_subckt(lines: list[str]) -> tuple[str, list[str], list[str]] | None:
    """If a ``.subckt`` block exists, return (name, pin_names, block_lines)."""
    start = None
    header = ""
    for i, line in enumerate(lines):
        s = line.strip()
        if s.lower().startswith(".subckt"):
            start = i
            header = s
            break
    if start is None:
        return None
    parts = header.split()
    name = parts[1] if len(parts) > 1 else "SUBCKT"
    pins = parts[2:] if len(parts) > 2 else []
    block = [lines[start]]
    for line in lines[start + 1 :]:
        block.append(line)
        if line.strip().lower().startswith(".ends"):
            break
    return name, pins, block


def netlist_to_subckt(
    text: str,
    *,
    name: str,
    pins: list[str] | None = None,
) -> tuple[str, list[str], str, list[str]]:
    """Convert an LTspice netlist into an ngspice ``.subckt`` block.

    Two modes:
      1. The netlist already defines a ``.subckt`` → extract it verbatim (its
         declared name/pins are authoritative; ``name``/``pins`` args only warn on
         mismatch).
      2. Flat test netlist → wrap element + ``.model`` lines into ``.subckt name
         <pins> … .ends``, dropping independent V/I stimulus and analysis
         directives (each drop is reported in warnings). ``pins`` is required here.

    Returns (subckt_text, warnings, sim_name, pin_names). ``sim_name`` and
    ``pin_names`` are those embedded in the emitted ``.subckt``.
    """
    lines, warnings = normalize_ltspice_netlist(text)

    extracted = _extract_subckt(lines)
    if extracted is not None:
        found_name, found_pins, block = extracted
        if found_name.upper() != name.upper():
            warnings.append(f"using subckt name {found_name!r} from netlist (requested {name!r})")
        if pins and pins != found_pins:
            warnings.append(
                f"using subckt pins {found_pins!r} from netlist (requested {pins!r})"
            )
        body = "\n".join(line for line in block if line.strip())
        return body + "\n", warnings, found_name, found_pins

    if not pins:
        raise ValueError(
            "flat netlist has no .subckt; --pins is required to wrap it "
            "(space-separated external node names)"
        )

    body: list[str] = []
    models: list[str] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("*"):
            continue
        if s.startswith("."):
            directive = s[1:].split()[0].lower() if len(s) > 1 else ""
            if directive in ("model", "param", "func"):
                models.append(s)
            # other kept directives (.include/.lib/.global) belong at top level, skip in body
            continue
        if _is_element(line):
            if s[0].upper() in _SOURCE_PREFIXES:
                warnings.append(f"dropped independent source (stimulus): {s.split()[0]}")
                continue
            body.append(s)

    if not body:
        raise ValueError("no device lines found to wrap into a subckt")

    pin_str = " ".join(pins)
    parts = [f".subckt {name} {pin_str}"]
    parts.extend(models)
    parts.extend(body)
    parts.append(f".ends {name}")
    return "\n".join(parts) + "\n", warnings, name, list(pins)


@dataclass
class LtspiceModelProvider:
    """Build an ngspice subckt from an LTspice-exported netlist (.net/.cir)."""

    net_path: Path
    sim_name: str
    pins: list[str] | None = None
    valid_range: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    notes: str | None = None

    source = ModelSource.LTSPICE

    def build(self, entry: ComponentMapping, *, workdir: Path) -> ModelArtifact:
        raw = self.net_path.read_text(encoding="utf-8", errors="replace")
        subckt_text, warnings, sim_name, pin_names = netlist_to_subckt(
            raw, name=self.sim_name, pins=self.pins
        )

        lib_path = (workdir / f"{sim_name}.lib").resolve()
        write_subckt(lib_path, subckt_text)

        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        note_parts = [p for p in (self.notes, *warnings) if p]
        provenance = ModelProvenance(
            source=ModelSource.LTSPICE,
            generated_at=datetime.now(timezone.utc).isoformat(),
            tool="benchgate ltspice provider",
            source_files=[str(self.net_path)],
            checksum=checksum,
            valid_range=dict(self.valid_range or {}),
            metrics=dict(self.metrics or {}),
            notes="; ".join(note_parts) or None,
        )
        if pin_names:
            pin_str = " ".join(pin_names)
        elif entry and entry.sim_pins:
            pin_str = entry.sim_pins
        else:
            pin_str = None
        return ModelArtifact(
            lib_path=lib_path,
            sim_name=sim_name,
            sim_pins=pin_str,
            provenance=provenance,
        )
