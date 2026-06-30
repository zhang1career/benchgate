"""Generate mcu_pwm_ctrl.lib from firmware board_config.h."""

from __future__ import annotations

import argparse
from pathlib import Path

from benchgate.cosim.board_config import load_board_config
from benchgate.cosim.pwm_drive import generate_mcu_pwm_lib
from benchgate.paths import benchgate_home


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate mcu_pwm_ctrl.lib from board_config.h")
    parser.add_argument(
        "--firmware",
        type=Path,
        required=True,
        help="Firmware tree containing Inc/board_config.h",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=benchgate_home() / "models" / "subckt" / "mcu_pwm_ctrl.lib",
        help="Output SPICE library path",
    )
    args = parser.parse_args(argv)
    cfg = load_board_config(args.firmware / "Inc" / "board_config.h")
    generate_mcu_pwm_lib(cfg, args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
