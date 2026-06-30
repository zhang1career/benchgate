"""Build and load firmware control.c as a host shared library."""

from __future__ import annotations

import ctypes
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path


class ControlSim:
    """ctypes wrapper around sim_control_shim + firmware control.c."""

    def __init__(self, lib_path: Path) -> None:
        self._lib = ctypes.CDLL(str(lib_path))
        self._lib.sim_control_init.argtypes = []
        self._lib.sim_control_init.restype = None
        self._lib.sim_control_reset.argtypes = []
        self._lib.sim_control_reset.restype = None
        self._lib.sim_control_set_mode.argtypes = [ctypes.c_int]
        self._lib.sim_control_set_mode.restype = None
        self._lib.sim_control_set_stage.argtypes = [ctypes.c_int]
        self._lib.sim_control_set_stage.restype = None
        self._lib.sim_control_set_vset.argtypes = [ctypes.c_float]
        self._lib.sim_control_set_vset.restype = None
        self._lib.sim_control_set_iset.argtypes = [ctypes.c_float]
        self._lib.sim_control_set_iset.restype = None
        self._lib.sim_control_set_enable.argtypes = [ctypes.c_int]
        self._lib.sim_control_set_enable.restype = None
        self._lib.sim_control_set_fault.argtypes = [ctypes.c_int]
        self._lib.sim_control_set_fault.restype = None
        self._lib.sim_control_update.argtypes = [
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
        ]
        self._lib.sim_control_update.restype = None
        self._lib.sim_control_get_duty.argtypes = []
        self._lib.sim_control_get_duty.restype = ctypes.c_float

    def init(
        self,
        *,
        mode: int = 2,
        stage: int = 0,
        v_set_v: float = 5.0,
        i_set_a: float = 1.0,
        enable: bool = True,
    ) -> None:
        self._lib.sim_control_init()
        self._lib.sim_control_set_mode(mode)
        self._lib.sim_control_set_stage(stage)
        self._lib.sim_control_set_vset(v_set_v)
        self._lib.sim_control_set_iset(i_set_a)
        self._lib.sim_control_set_enable(1 if enable else 0)
        self._lib.sim_control_set_fault(0)

    def update(self, vin_v: float, vout_v: float, iout_a: float = 0.0, temp_c: float = 25.0) -> float:
        self._lib.sim_control_update(
            ctypes.c_float(vin_v),
            ctypes.c_float(vout_v),
            ctypes.c_float(iout_a),
            ctypes.c_float(temp_c),
        )
        return float(self._lib.sim_control_get_duty())


def _library_name(sim_gains: str = "stock") -> str:
    base = "libsim_control"
    if sim_gains != "stock":
        base = f"{base}_{sim_gains}"
    if sys.platform == "darwin":
        return f"{base}.dylib"
    if sys.platform == "win32":
        return f"{base}.dll"
    return f"{base}.so"


def _patch_control_source(control: str, sim_gains: str) -> str:
    if sim_gains == "cc":
        return re.sub(
            r"pi_init\(&s_pi_i,\s*0\.02f,\s*5\.0f",
            "pi_init(&s_pi_i, 0.5f, 200.0f",
            control,
        )
    if sim_gains == "cv":
        control = re.sub(
            r"pi_init\(&s_pi_v,\s*0\.05f,\s*2\.0f",
            "pi_init(&s_pi_v, 0.12f, 15.0f",
            control,
        )
        control = re.sub(
            r"pi_init\(&s_pi_i,\s*0\.02f,\s*5\.0f",
            "pi_init(&s_pi_i, 0.5f, 200.0f",
            control,
        )
        # Fixed-duty cosim cannot deliver arbitrary i_limit; clamp to plausible Iload.
        return re.sub(
            r"(case MODE_CV: \{\n"
            r"        float i_limit = pi_update\(&s_pi_v, state->v_set_v, tel->vout_v, dt_s\);\n)"
            r"(\s*duty_cmd = pi_update\(&s_pi_i, i_limit, tel->iout_a, dt_s\);)",
            r"\1"
            r"        float i_plant_max = (state->v_set_v / 10.0f) * 1.25f;\n"
            r"        if (i_limit > i_plant_max) {\n"
            r"            i_limit = i_plant_max;\n"
            r"        }\n"
            r"\2",
            control,
            count=1,
        )
    return control


def _prepare_build_tree(firmware_dir: Path, build_dir: Path, *, sim_gains: str) -> tuple[Path, Path]:
    """Copy firmware sources with sim-friendly SOFTSTART_MS and optional PI tweaks."""
    src_dir = build_dir / "fw" / sim_gains / "Src"
    inc_dir = build_dir / "fw" / sim_gains / "Inc"
    src_dir.mkdir(parents=True, exist_ok=True)
    inc_dir.mkdir(parents=True, exist_ok=True)
    control = (firmware_dir / "Src" / "control.c").read_text(encoding="utf-8")
    control = _patch_control_source(control, sim_gains)
    (src_dir / "control.c").write_text(control, encoding="utf-8")
    board = (firmware_dir / "Inc" / "board_config.h").read_text(encoding="utf-8")
    board = re.sub(r"#define\s+SOFTSTART_MS\s+\d+U", "#define SOFTSTART_MS 5U", board)
    (inc_dir / "board_config.h").write_text(board, encoding="utf-8")
    shutil.copy2(firmware_dir / "Inc" / "control.h", inc_dir / "control.h")
    return src_dir / "control.c", inc_dir


def build_control_library(
    firmware_dir: Path,
    build_dir: Path,
    *,
    sim_gains: str = "stock",
    force: bool = False,
) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    out = build_dir / _library_name(sim_gains)
    shim = Path(__file__).resolve().parent / "native" / "sim_control_shim.c"
    control_c, include = _prepare_build_tree(firmware_dir, build_dir, sim_gains=sim_gains)
    if not control_c.exists():
        raise FileNotFoundError(f"missing firmware control source: {control_c}")

    inputs = [shim, control_c, include / "control.h", include / "board_config.h"]
    newest_in = max(p.stat().st_mtime for p in inputs if p.exists())
    if out.exists() and not force and out.stat().st_mtime >= newest_in:
        return out

    cmd = [
        "cc",
        "-shared",
        "-fPIC",
        "-O2",
        "-Wall",
        f"-I{include}",
        str(shim),
        str(control_c),
        "-o",
        str(out),
        "-lm",
    ]
    if platform.system() == "Darwin":
        cmd.insert(1, "-dynamiclib")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"control library build failed:\n{proc.stderr}")
    return out


def load_control_sim(
    firmware_dir: Path,
    build_dir: Path,
    *,
    sim_gains: str = "stock",
) -> ControlSim:
    lib = build_control_library(firmware_dir, build_dir, sim_gains=sim_gains)
    return ControlSim(lib)
