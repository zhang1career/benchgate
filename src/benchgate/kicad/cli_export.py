"""kicad-cli wrappers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_KICAD_CLI_CANDIDATES = (
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "/Applications/KiCad.app/Contents/MacOS/kicad-cli",
    "/usr/local/bin/kicad-cli",
    "/usr/bin/kicad-cli",
)


def find_kicad_cli() -> str:
    for env_name in ("KICAD_CLI", "KICAD_MCP_KICAD_CLI"):
        env_path = os.environ.get(env_name, "").strip()
        if env_path and Path(env_path).is_file():
            return env_path

    cli = shutil.which("kicad-cli")
    if cli:
        return cli

    for candidate in _KICAD_CLI_CANDIDATES:
        if Path(candidate).is_file():
            return candidate

    raise FileNotFoundError(
        "kicad-cli not found. Install KiCad 10, add it to PATH, "
        "or set KICAD_CLI=/path/to/kicad-cli"
    )


def export_spice_netlist(schematic: Path, output: Path) -> Path:
    cli = find_kicad_cli()
    output.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [cli, "sch", "export", "netlist", "--format", "spice", "-o", str(output), str(schematic)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"kicad-cli netlist export failed (exit {proc.returncode})"
            + (f":\n{detail}" if detail else "")
        )
    return output


def kicad_version() -> str:
    cli = find_kicad_cli()
    proc = subprocess.run(
        [cli, "version", "--format", "about"],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()
