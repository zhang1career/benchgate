"""Parameter sweep: run a netlist over a grid of overrides, collect metrics per run.

A sweep axis is either:
  * ``--param NAME=v1,v2,...`` : overrides a ``.param NAME=`` line (feeds {NAME} expansion)
  * ``--set REF=v1,v2,...``    : overrides the value (last field) of element line ``REF ...``

Two entry points share one grid engine, because the only thing that differs between
them is where the base netlist text comes from:

  * :func:`run_sweep`       - a KiCad design: export the schematic, then prepare it with
                              a ``sim_profiles.yaml`` profile's stimulus and excludes.
  * :func:`run_block_sweep` - a standalone ``.cir`` testbench around a block from
                              ``models/blocks/``, with no KiCad project and no profile.
                              This is what makes a block verifiable on its own, before
                              the board it belongs to has a schematic.

The base text is built once, then each grid point patches it, runs ngspice, and
evaluates every requested metric on that one raw file.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from benchgate.kicad.cli_export import export_spice_netlist
from benchgate.kicad.project import KiCadProject
from benchgate.sim.analysis import (
    _compute_metric,
    _resolve_signal,
    _window_slice,
    parse_ngspice_raw,
)
from benchgate.sim.netlist import prepare_netlist
from benchgate.sim.runner import run_ngspice


@dataclass
class SweepPoint:
    overrides: dict[str, str]
    metric: float
    passed: bool | None
    ngspice_ok: bool
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class SweepReport:
    metric: str
    profile: str
    points: list[dict]
    ran_at: str
    report_path: str | None = None
    metrics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_axis(spec: str) -> tuple[str, list[str]]:
    name, sep, vals = spec.partition("=")
    if not sep:
        raise ValueError(f"sweep axis must be NAME=v1,v2,... (got {spec!r})")
    values = [v.strip() for v in vals.split(",") if v.strip()]
    if not values:
        raise ValueError(f"sweep axis {name!r} has no values")
    return name.strip(), values


def parse_metric(spec: str) -> tuple[str, str, str | None]:
    """'signal[:metric[:window_after]]' -> (signal, metric, window)."""
    parts = spec.split(":")
    signal = parts[0].strip()
    metric = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "min"
    window = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    return signal, metric, window


def parse_metric_specs(specs: list[str]) -> dict[str, tuple[str, str, str | None]]:
    """``['bw=v(com):bw_3db', 'v(o):peaking_db']`` -> ``{name: (signal, metric, window)}``.

    A ``name=`` prefix is optional and only recognised before the first ``:``, so a
    signal that happens to contain ``=`` is still parsed as a signal. Without a
    prefix the whole spec is the name, which keeps report keys unambiguous.
    """
    out: dict[str, tuple[str, str, str | None]] = {}
    for spec in specs:
        spec = spec.strip()
        if not spec:
            continue
        head, sep, tail = spec.partition("=")
        if sep and ":" not in head:
            name, body = head.strip(), tail.strip()
        else:
            name, body = spec, spec
        if not body:
            raise ValueError(f"metric {spec!r} has no signal")
        if name in out:
            raise ValueError(f"duplicate metric name {name!r}")
        out[name] = parse_metric(body)
    if not out:
        raise ValueError("sweep needs at least one metric")
    return out


# .lib takes an optional section name after the path, hence the trailing group.
_INCLUDE_RE = re.compile(
    r"""^(\s*\.(?:include|inc|lib)\s+)(["']?)([^"'\s]+)(["']?)(.*)$""",
    re.MULTILINE | re.IGNORECASE,
)


def absolutize_includes(text: str, base_dir: Path) -> str:
    """Rewrite relative ``.include``/``.lib`` paths against ``base_dir``.

    ngspice runs the netlist from a scratch copy, so a path written relative to the
    testbench's own directory would not resolve there. Paths that do not exist under
    ``base_dir`` are left alone: they may be absolute already, or resolved by
    ngspice's own search path.
    """

    def repl(match: re.Match[str]) -> str:
        head, q1, path, q2, tail = match.groups()
        candidate = Path(path)
        if not candidate.is_absolute():
            resolved = (base_dir / candidate).resolve()
            if resolved.exists():
                path = str(resolved)
        quote = q1 or q2 or '"'
        return f"{head}{quote}{path}{quote}{tail}"

    return _INCLUDE_RE.sub(repl, text)


def apply_param(text: str, name: str, value: str) -> str:
    pat = re.compile(rf"^\.param\s+{re.escape(name)}\s*=.*$", re.MULTILINE | re.IGNORECASE)
    replacement = f".param {name}={value}"
    if pat.search(text):
        return pat.sub(replacement, text, count=1)
    # Not declared yet: inject after the .title line (or at the very top).
    if re.search(r"^\.title\b.*$", text, flags=re.MULTILINE | re.IGNORECASE):
        return re.sub(r"^(\.title\b.*)$", r"\1\n" + replacement, text, count=1, flags=re.MULTILINE | re.IGNORECASE)
    return replacement + "\n" + text


def apply_set(text: str, ref: str, value: str) -> str:
    pat = re.compile(rf"^({re.escape(ref)}\s+.*\s)(\S+)\s*$", re.MULTILINE)
    new_text, n = pat.subn(lambda m: f"{m.group(1)}{value}", text, count=1)
    if n == 0:
        raise ValueError(f"sweep --set: element {ref!r} not found in netlist")
    return new_text


def evaluate_metric(raw_path: Path, signal: str, metric: str, window: str | None) -> float:
    axis, signals = parse_ngspice_raw(raw_path)
    series = _resolve_signal(signals, signal)
    if series is None:
        return float("nan")
    mask = _window_slice(axis, window)
    return _compute_metric(series[mask], metric, axis=axis[mask])


def _evaluate_all(
    raw_path: Path, specs: dict[str, tuple[str, str, str | None]]
) -> dict[str, float]:
    """Every metric off one raw file, so a grid point costs one ngspice run."""
    try:
        axis, signals = parse_ngspice_raw(raw_path)
    except Exception:
        return {name: float("nan") for name in specs}
    out: dict[str, float] = {}
    for name, (signal, metric, window) in specs.items():
        try:
            series = _resolve_signal(signals, signal)
            if series is None:
                out[name] = float("nan")
                continue
            mask = _window_slice(axis, window)
            out[name] = _compute_metric(series[mask], metric, axis=axis[mask])
        except Exception:
            out[name] = float("nan")
    return out


def _run_grid(
    base_text: str,
    output_dir: Path,
    *,
    axes: list[tuple[str, str, list[str]]],
    specs: dict[str, tuple[str, str, str | None]],
    pass_gte: float | None,
    pass_lte: float | None,
) -> list[SweepPoint]:
    """Patch ``base_text`` per grid point, run ngspice, evaluate every metric.

    ``axes`` is ``[(kind, name, values)]`` with kind ``param`` or ``set``. Pass/fail is
    judged on the first metric only; the rest are recorded for context.
    """
    primary = next(iter(specs))
    points: list[SweepPoint] = []
    combos = list(itertools.product(*[values for _, _, values in axes])) if axes else [()]
    for idx, combo in enumerate(combos):
        text = base_text
        overrides: dict[str, str] = {}
        for (kind, name, _), value in zip(axes, combo):
            overrides[name] = value
            text = apply_param(text, name, value) if kind == "param" else apply_set(text, name, value)

        run_dir = output_dir / f"pt{idx:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        cir = run_dir / "point.cir"
        cir.write_text(text, encoding="utf-8")
        result = run_ngspice(cir, work_dir=run_dir)

        values = (
            _evaluate_all(result.raw_output, specs)
            if result.raw_output
            else {name: float("nan") for name in specs}
        )

        passed: bool | None = None
        if pass_gte is not None or pass_lte is not None:
            value = values[primary]
            passed = True
            if pass_gte is not None and not (value >= pass_gte):
                passed = False
            if pass_lte is not None and not (value <= pass_lte):
                passed = False

        points.append(
            SweepPoint(
                overrides=overrides,
                metric=values[primary],
                passed=passed,
                ngspice_ok=result.success,
                metrics=values,
            )
        )
    return points


def _axes(params: dict[str, list[str]], sets: dict[str, list[str]]) -> list[tuple[str, str, list[str]]]:
    """Params first, then sets: a stable, deterministic grid order."""
    return [("param", n, v) for n, v in params.items()] + [("set", n, v) for n, v in sets.items()]


def _write_report(report: SweepReport, output_dir: Path, filename: str) -> SweepReport:
    report_path = output_dir / filename
    report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    report.report_path = str(report_path)
    return report


def run_sweep(
    design_dir: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    sim_profile_path: Path,
    profile: str,
    metric_spec: str | None = None,
    metrics: list[str] | None = None,
    params: dict[str, list[str]] | None = None,
    sets: dict[str, list[str]] | None = None,
    pass_gte: float | None = None,
    pass_lte: float | None = None,
) -> SweepReport:
    """Sweep a KiCad design's netlist, prepared with a ``sim_profiles.yaml`` profile."""
    specs = parse_metric_specs(metrics or ([metric_spec] if metric_spec else []))
    output_dir.mkdir(parents=True, exist_ok=True)

    project = KiCadProject.load(design_dir)
    exported = output_dir / "exported.net"
    base_prepared = output_dir / "sweep_base.cir"
    export_spice_netlist(project.schematic, exported)
    prepare_netlist(
        exported,
        manifest_path,
        base_prepared,
        sim_profile_path=sim_profile_path,
        profile=profile,
    )

    points = _run_grid(
        base_prepared.read_text(encoding="utf-8"),
        output_dir,
        axes=_axes(params or {}, sets or {}),
        specs=specs,
        pass_gte=pass_gte,
        pass_lte=pass_lte,
    )
    report = SweepReport(
        metric=next(iter(specs)),
        profile=profile,
        points=[asdict(p) for p in points],
        ran_at=datetime.now(timezone.utc).isoformat(),
        metrics=list(specs),
    )
    return _write_report(report, output_dir, "sweep_report.json")


def run_block_sweep(
    netlist_path: Path,
    output_dir: Path,
    *,
    metrics: list[str],
    params: dict[str, list[str]] | None = None,
    sets: dict[str, list[str]] | None = None,
    pass_gte: float | None = None,
    pass_lte: float | None = None,
) -> SweepReport:
    """Sweep a standalone testbench ``.cir`` -- no KiCad project, no sim profile.

    The testbench owns its own stimulus and ``.control`` block, so an AC run is just
    an ``ac`` line in it and needs no support here. That is the point: a block under
    ``models/blocks/`` can be characterised before the board exists, and the numbers
    that land in ``blocks.yaml`` come from a run rather than from a person retyping
    them out of a private script.
    """
    netlist_path = netlist_path.resolve()
    if not netlist_path.is_file():
        raise FileNotFoundError(f"testbench netlist not found: {netlist_path}")
    specs = parse_metric_specs(metrics)
    output_dir.mkdir(parents=True, exist_ok=True)

    base_text = absolutize_includes(
        netlist_path.read_text(encoding="utf-8"), netlist_path.parent
    )
    (output_dir / "sweep_base.cir").write_text(base_text, encoding="utf-8")

    points = _run_grid(
        base_text,
        output_dir,
        axes=_axes(params or {}, sets or {}),
        specs=specs,
        pass_gte=pass_gte,
        pass_lte=pass_lte,
    )
    report = SweepReport(
        metric=next(iter(specs)),
        profile=netlist_path.name,
        points=[asdict(p) for p in points],
        ran_at=datetime.now(timezone.utc).isoformat(),
        metrics=list(specs),
    )
    return _write_report(report, output_dir, "block_sweep_report.json")
