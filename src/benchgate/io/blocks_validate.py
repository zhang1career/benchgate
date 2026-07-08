"""Validate ``models/blocks.yaml`` before pipeline sync or long MC runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from benchgate.io.blocks_config import circuit_spec_to_checks, load_blocks_yaml
from benchgate.sim.profile import load_profile_block, load_profile_checks
from benchgate.sim.tolerance_layers import merge_layer_plan
from benchgate.sim.tolerance_sim import load_tolerance_sim_config

IssueLevel = Literal["error", "warn", "info"]


@dataclass
class ValidationIssue:
    level: IssueLevel
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class BlocksValidationReport:
    ok: bool
    blocks_yaml: str
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    info: list[ValidationIssue] = field(default_factory=list)
    mc_layers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocks_yaml": self.blocks_yaml,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "mc_layers": self.mc_layers,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
            "info": [i.to_dict() for i in self.info],
        }

    def add(self, level: IssueLevel, path: str, message: str) -> None:
        issue = ValidationIssue(level, path, message)
        if level == "error":
            self.errors.append(issue)
        elif level == "warn":
            self.warnings.append(issue)
        else:
            self.info.append(issue)


def _parse_spice_time(text: str) -> float:
    """Parse ngspice-style time literals (e.g. ``45m`` → 45 ms)."""
    t = str(text).strip().lower()
    for suffix, scale in (
        ("ms", 1e-3),
        ("us", 1e-6),
        ("u", 1e-6),
        ("ns", 1e-9),
        ("n", 1e-9),
        ("ps", 1e-12),
        ("p", 1e-12),
        ("s", 1.0),
        ("m", 1e-3),
    ):
        if t.endswith(suffix):
            return float(t[: -len(suffix)]) * scale
    return float(t)


def _collect_checks(blocks: dict[str, Any]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    spec = blocks.get("circuit_spec")
    if isinstance(spec, dict):
        for chk in circuit_spec_to_checks(spec):
            out.append(("circuit_spec", chk))
    for i, block in enumerate(blocks.get("blocks") or []):
        if not isinstance(block, dict):
            continue
        bspec = block.get("circuit_spec")
        if isinstance(bspec, dict):
            ref = block.get("reference") or block.get("kicad_key") or str(i)
            for chk in circuit_spec_to_checks(bspec):
                out.append((f"blocks[{ref}].circuit_spec", chk))
    return out


def _validate_tolerance_axis(report: BlocksValidationReport, raw: dict, path: str) -> None:
    if not raw.get("ref"):
        report.add("error", path, "missing ref")
    dist = raw.get("distribution", "uniform")
    if dist not in {"uniform", "normal"}:
        report.add("warn", path, f"unknown distribution {dist!r}")
    if raw.get("mix"):
        weights = [float(o.get("weight", 0)) for o in raw["mix"] if isinstance(o, dict)]
        if weights and abs(sum(weights) - 1.0) > 0.01:
            report.add("warn", path, f"mix weights sum to {sum(weights):.3f}, expected ~1.0")
    elif raw.get("tolerance_pct") is None:
        report.add("error", path, "needs tolerance_pct or mix[]")


def _validate_environment_axis(report: BlocksValidationReport, raw: dict, path: str) -> None:
    if not raw.get("id"):
        report.add("error", path, "missing id")
    apply = raw.get("apply")
    if apply not in {"param", "temp"}:
        report.add("error", path, f"apply must be param|temp, got {apply!r}")
    if apply == "param" and not raw.get("param"):
        report.add("error", path, "param apply requires param name")


def validate_blocks_yaml(
    design_dir: Path,
    blocks_yaml: Path,
    *,
    sim_profile_path: Path | None = None,
    profile: str = "default",
) -> BlocksValidationReport:
    """Static checks for blocks.yaml, MC layers, and tolerance_sim vs metric windows."""
    design_dir = design_dir.resolve()
    blocks_yaml = blocks_yaml.resolve()
    report = BlocksValidationReport(ok=True, blocks_yaml=str(blocks_yaml))

    if not blocks_yaml.is_file():
        report.add("error", "blocks.yaml", f"file not found: {blocks_yaml}")
        report.ok = False
        return report

    try:
        blocks = load_blocks_yaml(blocks_yaml)
    except Exception as exc:
        report.add("error", "blocks.yaml", f"YAML parse failed: {exc}")
        report.ok = False
        return report

    if not blocks:
        report.add("warn", "blocks.yaml", "empty or missing content")
        report.ok = False
        return report

    models_dir = design_dir / "models"

    for i, item in enumerate(blocks.get("circuit_spec", {}).get("checks") or []):
        path = f"circuit_spec.checks[{i}]"
        if not isinstance(item, dict):
            report.add("error", path, "must be a mapping")
            continue
        if not item.get("signal"):
            report.add("error", path, "missing signal")
        bounds = item.get("bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            report.add("error", path, "bounds must be [lo, hi]")

    for i, raw in enumerate(blocks.get("tolerances") or []):
        if isinstance(raw, dict):
            _validate_tolerance_axis(report, raw, f"tolerances[{i}]")

    for i, raw in enumerate(blocks.get("environment") or []):
        if isinstance(raw, dict):
            _validate_environment_axis(report, raw, f"environment[{i}]")

    for i, block in enumerate(blocks.get("blocks") or []):
        path = f"blocks[{i}]"
        if not isinstance(block, dict):
            report.add("error", path, "must be a mapping")
            continue
        if not (block.get("kicad_key") or block.get("reference")):
            report.add("warn", path, "missing kicad_key and reference")
        source = block.get("source")
        if source:
            src = Path(source)
            if not src.is_absolute():
                src = models_dir / src
            if not src.is_file():
                report.add("error", f"{path}.source", f"not found: {src}")
        if block.get("metrics_file"):
            mf = models_dir / str(block["metrics_file"])
            if not mf.is_file():
                report.add("error", f"{path}.metrics_file", f"not found: {mf}")
        for j, raw in enumerate(block.get("tolerances") or []):
            if isinstance(raw, dict):
                _validate_tolerance_axis(report, raw, f"{path}.tolerances[{j}]")

    layers = merge_layer_plan(blocks, models_dir)
    report.mc_layers = [layer.id for layer in layers]
    tol = blocks.get("tolerances") or []
    env = blocks.get("environment") or []
    if (tol or env) and not layers:
        report.add("error", "mc_layers", "tolerances/environment defined but no MC layer plan")

    if layers:
        profile_block: dict[str, Any] = {}
        if sim_profile_path and sim_profile_path.is_file():
            profile_block = load_profile_block(sim_profile_path, profile)
        sim_cfg = load_tolerance_sim_config(blocks, profile_block)
        if sim_cfg.tier not in {"auto", "coarse", "fine"}:
            report.add("error", "tolerance_sim.tier", f"unknown tier {sim_cfg.tier!r}")
        margin = sim_cfg.refine_margin_pct
        if margin < 0 or margin > 100:
            report.add("error", "tolerance_sim.refine_margin_pct", f"out of range: {margin}")

        checks = _collect_checks(blocks)
        if not checks:
            fallback = (
                load_profile_checks(sim_profile_path, profile)
                if sim_profile_path and sim_profile_path.is_file()
                else []
            )
            if fallback:
                checks = [("sim_profile", c) for c in fallback]
                report.add("info", "checks", f"using {len(fallback)} checks from sim profile {profile!r}")
            else:
                report.add("error", "checks", "no circuit_spec checks and no sim profile checks")

        max_window_s = 0.0
        for path, chk in checks:
            wa = chk.get("window_after")
            if wa is not None:
                try:
                    max_window_s = max(max_window_s, _parse_spice_time(str(wa)))
                except ValueError:
                    report.add("error", f"{path}.window_after", f"invalid time {wa!r}")

        for preset_name, preset in (("coarse", sim_cfg.coarse), ("fine", sim_cfg.fine)):
            if preset and preset.tran_stop:
                try:
                    stop_s = _parse_spice_time(preset.tran_stop)
                    if max_window_s > 0 and stop_s < max_window_s:
                        report.add(
                            "error",
                            f"tolerance_sim.{preset_name}.tran_stop",
                            f"{preset.tran_stop} ({stop_s}s) shorter than max window_after "
                            f"({max_window_s}s); coarse metrics will be invalid",
                        )
                except ValueError:
                    report.add(
                        "error",
                        f"tolerance_sim.{preset_name}.tran_stop",
                        f"invalid time {preset.tran_stop!r}",
                    )

    report.ok = len(report.errors) == 0
    return report
