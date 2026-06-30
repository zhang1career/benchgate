"""Run ngspice in batch mode and collect results."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from benchgate.paths import benchgate_tmp_root


@dataclass
class SimResult:
    success: bool
    stdout: str
    stderr: str
    raw_output: Path | None
    log_path: Path | None


def find_ngspice() -> str:
    path = shutil.which("ngspice")
    if not path:
        raise FileNotFoundError("ngspice not found on PATH; install ngspice first")
    return path


def _temp_run_dir() -> Path:
    run_dir = benchgate_tmp_root() / uuid.uuid4().hex[:12]
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def run_ngspice(
    netlist_path: Path,
    control_extra: str = "",
    work_dir: Path | None = None,
) -> SimResult:
    """
    Batch-run ngspice on a prepared netlist.

    Appends default transient analysis if netlist has no .control block.
    """
    ngspice = find_ngspice()
    work = work_dir or netlist_path.parent
    log_path = work / "ngspice.log"
    raw_path = work / "sim.raw"

    netlist = netlist_path.read_text(encoding="utf-8", errors="replace")
    if ".control" not in netlist.lower() and " tran " not in netlist.lower():
        netlist += """
.control
tran 1u 10m
run
write sim.raw all
.endc
.end
"""

    tmp_dir = _temp_run_dir()
    cir: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".cir",
            dir=tmp_dir,
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(netlist)
            if control_extra:
                tmp.write("\n" + control_extra)
            cir = Path(tmp.name)

        cmd = [ngspice, "-b", "-o", str(log_path.resolve()), str(cir.resolve())]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=work)

        sim_ok = proc.returncode == 0
        if sim_ok and log_path.exists():
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            if (
                "Transient op failed" in log_text
                or "tran simulation(s) aborted" in log_text
                or "No. of Data Rows : 0" in log_text
            ):
                sim_ok = False

        return SimResult(
            success=sim_ok,
            stdout=proc.stdout,
            stderr=proc.stderr,
            raw_output=raw_path if raw_path.exists() else None,
            log_path=log_path if log_path.exists() else None,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
