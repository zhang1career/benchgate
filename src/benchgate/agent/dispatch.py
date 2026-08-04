"""Dispatch agent tool calls to benchgate modules."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchgate.agent.tools import TOOLS
from benchgate.gate.report import write_gate_report
from benchgate.io.manifest import load_manifest, save_manifest
from benchgate.rules.loader import default_rule_pack_paths
from benchgate.kicad.project import KiCadProject
from benchgate.kicad.spice_fields import apply_model_to_reference
from benchgate.lab.capture import LabSession, capture_and_fit
from benchgate.lab.fit import measured_to_subckt, write_subckt
from benchgate.lab.store import LabDataStore
from benchgate.mapping.engine import apply_measured_model, mapping_status, sync_project
from benchgate.paths import benchgate_paths, resolve_project_path
from benchgate.schemas import ComponentMapping, MappingManifest, ModelProvenance, ModelSource
from benchgate.watch.trigger import watch_once


def _paths_for_design(design_dir: str | Path, args: dict[str, Any]):
    return benchgate_paths(
        design_dir,
        manifest=args.get("manifest_path"),
    )


def _role_overrides(args: dict[str, Any]) -> dict[str, str | None]:
    return {role: args[role] for role in ("scope", "dmm", "awg", "sa", "rfgen", "vna") if args.get(role)}


def _open_bench(paths, args: dict[str, Any]):
    from benchgate.instruments import load_bench

    return load_bench(
        paths.instruments,
        project_lab_path=paths.lab_config,
        overrides=_role_overrides(args),
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _resolve_provenance_dict(args: dict[str, Any], key: str, existing: dict | None) -> dict:
    """Merge agent/CLI dict fields; ``None`` or absent → keep existing (or {})."""
    fallback = existing if isinstance(existing, dict) else {}
    if key not in args:
        return fallback
    val = args[key]
    if val is None:
        return fallback
    return val


def dispatch(name: str, args: dict[str, Any]) -> Any:
    if name not in TOOLS:
        raise KeyError(f"Unknown tool: {name}")

    if name == "mapping_sync":
        p = _paths_for_design(args["design_dir"], args)
        manifest = sync_project(
            p.design,
            p.manifest,
            p.models,
            subckt_dir=p.subckt,
            global_models_dir=p.global_models,
        )
        return mapping_status(manifest)

    if name == "mapping_status":
        p = _paths_for_design(args.get("design_dir", "design"), args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        return mapping_status(load_manifest(mp, global_models_dir=p.global_models))

    if name == "model_build":
        from benchgate.mapping.engine import build_model
        from benchgate.providers.factory import create_model_provider
        from benchgate.providers.meas_log import merge_metrics, parse_meas_file

        p = _paths_for_design(args["design_dir"], args)
        provider_name = args.get("provider", "ltspice")
        source_file = (
            resolve_project_path(p.design, args["source_file"], p.design)
            if args.get("source_file")
            else None
        )
        pins = args["pins"].split() if args.get("pins") else None
        manifest = (
            load_manifest(p.manifest, global_models_dir=p.global_models)
            if p.manifest.exists()
            else MappingManifest()
        )
        entry = manifest.find(args["kicad_key"]) or ComponentMapping(kicad_key=args["kicad_key"])
        existing = entry.provenance
        valid_range = _resolve_provenance_dict(args, "valid_range", existing.valid_range if existing else None)
        log_metrics: dict[str, float] = {}
        if args.get("from_meas"):
            meas_path = resolve_project_path(p.design, args["from_meas"], p.design)
            log_metrics = parse_meas_file(meas_path)
        base_metrics = _resolve_provenance_dict(args, "metrics", existing.metrics if existing else None)
        metrics = merge_metrics(log_metrics, base_metrics)
        sim_name = args.get("sim_name") or args.get("mpn") or entry.metadata.get("value")
        lib_path = None
        if args.get("lib_path"):
            lib_path = resolve_project_path(p.design, args["lib_path"], p.design)
        elif provider_name in ("bench", "vendor") and args.get("source_file"):
            lib_path = resolve_project_path(p.design, args["source_file"], p.design)
        provider = create_model_provider(
            provider_name,
            entry=entry,
            source_file=source_file,
            sim_name=sim_name,
            pins=pins,
            mpn=args.get("mpn"),
            lib_path=lib_path,
            valid_range=valid_range,
            metrics=metrics,
            notes=args.get("notes"),
            sim_pins=args.get("sim_pins"),
        )
        if args.get("reference"):
            entry.reference = args["reference"]
        entry = build_model(manifest, entry, provider, workdir=p.subckt)
        save_manifest(manifest, p.manifest, global_models_dir=p.global_models)
        prov = entry.provenance
        return {
            "kicad_key": entry.kicad_key,
            "provider": provider_name,
            "source": prov.source.value if prov else None,
            "subckt": str(entry.sim_library),
            "sim_name": entry.sim_name,
            "sim_pins": entry.sim_pins,
            "metrics": prov.metrics if prov else {},
            "warnings": prov.notes if prov else None,
        }

    if name == "spec_set":
        p = _paths_for_design(args["design_dir"], args)
        manifest = (
            load_manifest(p.manifest, global_models_dir=p.global_models)
            if p.manifest.exists()
            else MappingManifest()
        )
        entry = manifest.find(args["kicad_key"]) or ComponentMapping(kicad_key=args["kicad_key"])
        if args.get("reference"):
            entry.reference = args["reference"]
        entry.spec = args["spec"]
        manifest.upsert(entry)
        save_manifest(manifest, p.manifest, global_models_dir=p.global_models)
        return {"kicad_key": entry.kicad_key, "spec": entry.spec}

    if name == "model_status":
        p = _paths_for_design(args.get("design_dir", "design"), args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        manifest = load_manifest(mp, global_models_dir=p.global_models)
        return {
            "entries": [
                {
                    "kicad_key": e.kicad_key,
                    "reference": e.reference,
                    "status": e.status,
                    "spice_kind": e.spice_kind.value,
                    "source": e.provenance.source.value if e.provenance else None,
                    "sim_name": e.sim_name,
                    "valid_range": e.provenance.valid_range if e.provenance else {},
                    "metrics": e.provenance.metrics if e.provenance else {},
                    "spec": e.spec or {},
                }
                for e in manifest.entries
            ]
        }

    if name == "pipeline_sync":
        from benchgate.pipeline.local_blocks import sync_local_blocks

        p = _paths_for_design(args["design_dir"], args)
        return sync_local_blocks(
            models_dir=p.models,
            manifest_path=p.manifest,
            subckt_dir=p.subckt,
            global_models_dir=p.global_models,
            blocks_yaml=p.blocks_yaml,
            tmp_dir=p.tmp_root / "pipeline",
        )

    if name == "lab_capture":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        store = LabDataStore(p.captured)
        ref = args["component_ref"]
        mpn = args["mpn"]
        kicad_key = args["kicad_key"]
        tags = args.get("tags") or ["characterize"]
        with LabSession(bench) as session:
            measured, meta = capture_and_fit(
                session,
                store,
                component_ref=ref,
                mpn=mpn,
                kicad_key=kicad_key,
                design=str(p.design),
                tags=tags,
            )
        subckt_name = f"MEAS_{ref}".upper()
        subckt_path = p.subckt / f"{subckt_name}.lib"
        write_subckt(subckt_path, measured_to_subckt(measured.params, subckt_name))
        manifest = (
            load_manifest(p.manifest, global_models_dir=p.global_models)
            if p.manifest.exists()
            else MappingManifest()
        )
        entry = apply_measured_model(
            manifest,
            kicad_key,
            lib_path=subckt_path.relative_to(p.global_models),
            sim_name=subckt_name,
            measured_params=measured.params,
        )
        entry.reference = ref
        entry.provenance = ModelProvenance(
            source=ModelSource.BENCH,
            generated_at=measured.captured_at,
            tool="benchgate lab fit",
            metrics=dict(measured.params),
            measured=measured,
        )
        save_manifest(manifest, p.manifest, global_models_dir=p.global_models)
        out = {
            "measured": measured.params,
            "subckt": str(subckt_path),
            "kicad_key": kicad_key,
            "session_id": meta.session_id,
            "tags": tags,
        }
        if args.get("rerun_sim", True):
            from benchgate.sim.pipeline import run_project_sim

            sim_dir = p.reports / "sim"
            sim_report, _ = run_project_sim(
                p.design,
                p.manifest,
                sim_dir,
                sim_profile_path=p.sim_profile,
                profile=str(args.get("profile", "default")),
            )
            gate_path = p.reports / "gate_report.json"
            gate = write_gate_report(
                p.manifest,
                gate_path,
                captured_dir=p.captured,
                sim_dir=sim_dir,
                sim_report_path=sim_dir / "sim_report.json",
                sim_profile_path=p.sim_profile,
                profile=str(args.get("profile", "default")),
            )
            out["sim"] = sim_report.to_dict()
            out["gate"] = gate.to_dict()
        return out

    if name == "lab_list":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        return {
            "instruments": {
                n: {"driver": c.driver, "address": c.address, "transport": c.transport}
                for n, c in bench.instruments.items()
            },
            "roles": bench.roles,
            "defaults": bench.defaults,
        }

    if name == "lab_read":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role=args.get("role", "dmm"), instrument=args.get("instrument"))
        count = int(args.get("count", 1))
        readings = []
        try:
            for _ in range(max(1, count)):
                r = inst.read()  # type: ignore[attr-defined]
                readings.append(
                    {
                        "value": r.value,
                        "unit": r.unit,
                        "quantity": r.quantity.value,
                        "normalized_value": r.normalized_value,
                        "normalized_unit": r.normalized_unit,
                        "flags": {k: v for k, v in r.flags.items() if v},
                        "timestamp": r.timestamp.isoformat(),
                    }
                )
        finally:
            inst.disconnect()
        return {"instrument": inst.name, "readings": readings}

    if name == "lab_capture_waveform":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        store = LabDataStore(p.captured)
        with LabSession(bench) as session:
            channel = int(args.get("channel", session.config.scope_channel))
            wf = session.capture_step_response(channel)
            meta = store.write_session(
                component_ref=args.get("component_ref"),
                design=str(p.design),
                waveforms={"scope_ch1": wf},
                roles=session.roles_map(),
                instruments=session.provenance(),
                tags=args.get("tags"),
            )
        return {
            "session_id": meta.session_id,
            "channel": wf.channel,
            "samples": len(wf),
            "sample_rate_hz": wf.sample_rate_hz,
            "path": str((meta.path or p.captured) / "scope_ch1.npz"),
        }

    if name == "lab_sa_sweep":
        from benchgate.instruments.types import ScanConfig

        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="sa", instrument=args.get("instrument"))
        store = LabDataStore(p.captured)
        try:
            cfg = ScanConfig(
                center_mhz=args.get("center_mhz"),
                span_mhz=args.get("span_mhz"),
                start_mhz=args.get("start_mhz"),
                stop_mhz=args.get("stop_mhz"),
                reference_dbm=args.get("reference_dbm"),
                attenuation=args.get("attenuation"),
            )
            if any(
                v is not None
                for v in (
                    cfg.center_mhz,
                    cfg.span_mhz,
                    cfg.start_mhz,
                    cfg.stop_mhz,
                    cfg.reference_dbm,
                    cfg.attenuation,
                )
            ):
                inst.configure_scan(cfg)  # type: ignore[attr-defined]
            spec = inst.capture_spectrum()  # type: ignore[attr-defined]
            meta = store.write_session(
                component_ref=args.get("component_ref"),
                design=str(p.design),
                spectra={"sa_trace": spec},
                roles={"sa": bench.instrument_for_role("sa")},
                instruments={"sa": inst.info.as_provenance("sa").get("sa_idn", inst.identify())},
                tags=args.get("tags"),
            )
        finally:
            inst.disconnect()
        return {
            "session_id": meta.session_id,
            "trace": spec.trace,
            "points": len(spec),
            "freq_start_hz": float(spec.freq_hz[0]) if len(spec) else None,
            "freq_stop_hz": float(spec.freq_hz[-1]) if len(spec) else None,
            "path": str((meta.path or p.captured) / "sa_trace.npz"),
            "metadata": spec.metadata,
        }

    if name == "lab_sa_peak":
        from benchgate.instruments.types import PeakMode

        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="sa", instrument=args.get("instrument"))
        try:
            mode = PeakMode(args.get("mode", "AVR"))
            r = inst.measure_peak(mode)  # type: ignore[attr-defined]
        finally:
            inst.disconnect()
        return {
            "instrument": inst.name,
            "peak": {
                "value": r.value,
                "unit": r.unit,
                "mode": mode.value,
                "timestamp": r.timestamp.isoformat(),
            },
        }

    if name == "lab_sa_floor":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="sa", instrument=args.get("instrument"))
        try:
            r = inst.measure_floor()  # type: ignore[attr-defined]
        finally:
            inst.disconnect()
        return {
            "instrument": inst.name,
            "floor": {
                "value": r.value,
                "unit": r.unit,
                "timestamp": r.timestamp.isoformat(),
            },
        }

    if name == "lab_sa_gen":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="rfgen", instrument=args.get("instrument"))
        try:
            if "enabled" in args:
                inst.set_generator_enabled(bool(args["enabled"]))  # type: ignore[attr-defined]
            if args.get("frequency_mhz") is not None:
                inst.set_generator_frequency_mhz(float(args["frequency_mhz"]))  # type: ignore[attr-defined]
            if args.get("power_dbm") is not None:
                inst.set_generator_power_dbm(int(args["power_dbm"]))  # type: ignore[attr-defined]
            if args.get("attenuator") is not None:
                inst.set_generator_attenuator(int(args["attenuator"]))  # type: ignore[attr-defined]
            status = {
                "enabled": inst.query_generator_enabled(),  # type: ignore[attr-defined]
                "frequency_mhz": inst.query_generator_frequency_mhz(),  # type: ignore[attr-defined]
            }
        finally:
            inst.disconnect()
        return {"instrument": inst.name, "generator": status}

    if name == "lab_sa_cal":
        from benchgate.instruments.types import CalStandard, SparamKind

        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="vna", instrument=args.get("instrument"))
        try:
            param = SparamKind(args["param"])
            standard = CalStandard(args.get("standard", "OPEN"))
            enabled = bool(args.get("enabled", True))
            inst.calibrate_sparam(param, standard, enabled=enabled)  # type: ignore[attr-defined]
        finally:
            inst.disconnect()
        return {
            "instrument": inst.name,
            "calibration": {
                "param": param.value,
                "standard": standard.value,
                "enabled": enabled,
            },
        }

    if name == "lab_sa_sparam":
        from benchgate.instruments.types import SparamKind

        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        inst = bench.select(role="vna", instrument=args.get("instrument"))
        store = LabDataStore(p.captured)
        try:
            param = SparamKind(args.get("param", "S21"))
            spec = inst.capture_sparam_trace(param)  # type: ignore[attr-defined]
            meta = store.write_session(
                component_ref=args.get("component_ref"),
                design=str(p.design),
                spectra={f"sa_{param.value.lower()}": spec},
                roles={"vna": bench.instrument_for_role("vna")},
                instruments={"vna": inst.info.as_provenance("vna").get("vna_idn", inst.identify())},
                tags=args.get("tags"),
            )
        finally:
            inst.disconnect()
        return {
            "session_id": meta.session_id,
            "param": param.value,
            "points": len(spec),
            "path": str((meta.path or p.captured) / f"sa_{param.value.lower()}.npz"),
            "metadata": spec.metadata,
        }

    if name == "lab_query_sessions":
        p = _paths_for_design(args["design_dir"], args)
        store = LabDataStore(p.captured)
        metas = store.list_sessions(
            component_ref=args.get("component_ref"),
            since=_parse_iso(args.get("since")),
            until=_parse_iso(args.get("until")),
            tags=args.get("tags"),
        )
        return {"sessions": [m.to_dict() for m in metas]}

    if name == "lab_metric_series":
        p = _paths_for_design(args["design_dir"], args)
        store = LabDataStore(p.captured)
        rows = store.metric_series(
            args["metric"],
            component_ref=args.get("component_ref"),
            since=_parse_iso(args.get("since")),
            until=_parse_iso(args.get("until")),
        )
        return {
            "metric": args["metric"],
            "series": [
                {
                    "captured_at": r["captured_at"].isoformat(),
                    "session_id": r["session_id"],
                    "component_ref": r["component_ref"],
                    "value": r["value"],
                }
                for r in rows
            ],
        }

    if name == "lab_metric_drift":
        from benchgate.lab.analyze import drift, metric_stats

        p = _paths_for_design(args["design_dir"], args)
        store = LabDataStore(p.captured)
        rows = store.metric_series(
            args["metric"],
            component_ref=args.get("component_ref"),
            since=_parse_iso(args.get("since")),
            until=_parse_iso(args.get("until")),
        )
        return {
            "metric": args["metric"],
            "stats": metric_stats(rows).to_dict(),
            "drift": drift(rows).to_dict(),
        }

    if name == "lab_apply_model":
        p = _paths_for_design(args["design_dir"], args)
        project = KiCadProject.load(p.design)
        lib = Path(args["sim_library"])
        if not lib.is_absolute():
            lib = (p.global_models / lib).resolve()
        apply_model_to_reference(
            project.schematic,
            args["reference"],
            sim_library=lib,
            sim_name=args["sim_name"],
            sim_pins=args.get("sim_pins", ""),
        )
        manifest = (
            load_manifest(p.manifest, global_models_dir=p.global_models)
            if p.manifest.exists()
            else MappingManifest()
        )
        apply_measured_model(
            manifest,
            args["kicad_key"],
            lib_path=lib.relative_to(p.global_models) if lib.is_relative_to(p.global_models) else lib,
            sim_name=args["sim_name"],
            sim_pins=args.get("sim_pins", ""),
        )
        entry = manifest.find(args["kicad_key"])
        if entry:
            entry.reference = args["reference"]
        save_manifest(manifest, p.manifest, global_models_dir=p.global_models)
        return {"ok": True, "reference": args["reference"]}

    if name == "sim_run":
        from benchgate.sim.pipeline import run_project_sim
        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out_dir = Path(args["output_dir"]) if args.get("output_dir") else p.reports / "sim"
        if args.get("output_dir") and not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "sim")
        elif not args.get("output_dir"):
            out_dir = p.reports / "sim"
        profile = args.get("profile", "default")
        report, result = run_project_sim(
            p.design,
            mp,
            out_dir,
            sim_profile_path=p.sim_profile,
            profile=profile,
            fail_on_preflight_error=bool(args.get("fail_on_preflight")),
        )
        return {
            "success": report.success,
            "report": report.to_dict(),
            "stderr": result.stderr,
        }

    if name == "sim_stress_sweep":
        from benchgate.sim.stress_sweep import run_stress_sweep

        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out_dir = Path(args["output_dir"]) if args.get("output_dir") else p.reports / "stress_sweep"
        if args.get("output_dir") and not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "stress_sweep")
        elif not args.get("output_dir"):
            out_dir = p.reports / "stress_sweep"
        report = run_stress_sweep(
            p.design,
            mp,
            out_dir,
            sim_profile_path=p.sim_profile,
            profile=args.get("profile", "default"),
        )
        return {"success": bool(report.to_dict().get("worst", {}).get("passed")), "report": report.to_dict()}

    if name == "sim_sweep":
        from benchgate.sim.sweep import run_sweep

        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out_dir = Path(args["output_dir"]) if args.get("output_dir") else p.reports / "sim_sweep"
        if args.get("output_dir") and not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "sim_sweep")
        elif not args.get("output_dir"):
            out_dir = p.reports / "sim_sweep"
        def norm(d: dict | None) -> dict[str, list[str]]:
            return {k: [str(x) for x in v] for k, v in (d or {}).items()}

        report = run_sweep(
            p.design,
            mp,
            out_dir,
            sim_profile_path=p.sim_profile,
            profile=args.get("profile", "default"),
            metric_spec=args.get("metric"),
            metrics=[str(m) for m in args.get("metrics") or []] or None,
            params=norm(args.get("params")),
            sets=norm(args.get("sets")),
            pass_gte=args.get("pass_gte"),
            pass_lte=args.get("pass_lte"),
        )
        return {"success": True, "report": report.to_dict()}

    if name == "sim_block_sweep":
        from benchgate.sim.sweep import run_block_sweep

        p = _paths_for_design(args["design_dir"], args)
        netlist = Path(args["netlist"])
        if not netlist.is_absolute():
            netlist = (p.design / netlist).resolve()
        out_dir = Path(args["output_dir"]) if args.get("output_dir") else p.reports / "block_sweep"
        if not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "block_sweep")

        def norm_block(d: dict | None) -> dict[str, list[str]]:
            return {k: [str(x) for x in v] for k, v in (d or {}).items()}

        metrics = [str(m) for m in args.get("metrics") or []]
        if not metrics and args.get("metric"):
            metrics = [str(args["metric"])]
        report = run_block_sweep(
            netlist,
            out_dir,
            metrics=metrics,
            params=norm_block(args.get("params")),
            sets=norm_block(args.get("sets")),
            pass_gte=args.get("pass_gte"),
            pass_lte=args.get("pass_lte"),
        )
        payload = report.to_dict()
        failed = [pt for pt in payload["points"] if pt.get("passed") is False]
        return {"success": not failed, "report": payload}

    if name == "sim_cosim":
        from benchgate.cosim.runner import run_cosim

        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out_dir = Path(args["output_dir"]) if args.get("output_dir") else p.reports / "sim_cosim"
        if args.get("output_dir") and not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "sim_cosim")
        elif not args.get("output_dir"):
            out_dir = p.reports / "sim_cosim"
        profile = args.get("profile", "hbridge_pwm_closed")
        report, result = run_cosim(
            p.design,
            mp,
            out_dir,
            sim_profile_path=p.sim_profile,
            profile=profile,
            build_dir=p.cosim_build,
        )
        return {
            "success": report.success,
            "report": report.to_dict(),
            "stderr": result.stderr,
        }

    if name == "gate_report":
        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out = Path(args.get("output_path", p.reports / "gate_report.json"))
        if not out.is_absolute():
            out = resolve_project_path(p.design, out, p.reports / "gate_report.json")
        sim_raw = Path(args["sim_raw_path"]) if args.get("sim_raw_path") else None
        if sim_raw and not sim_raw.is_absolute():
            sim_raw = resolve_project_path(p.design, sim_raw, p.reports / "sim" / "sim_waveform.csv")
        stress_sweep_path: Path | None = None
        if args.get("stress_sweep"):
            from benchgate.sim.stress_sweep import run_stress_sweep

            profile = str(args.get("profile", "default"))
            sweep_dir = p.reports / "stress_sweep"
            sweep_report = run_stress_sweep(
                p.design,
                mp,
                sweep_dir,
                sim_profile_path=p.sim_profile,
                profile=profile,
            )
            stress_sweep_path = Path(sweep_report.report_path) if sweep_report.report_path else None
        rule_pack_paths = None
        if args.get("rules") != "none":
            rule_pack_paths = default_rule_pack_paths(home=p.home, design=p.design)
        mc_path = p.reports / "mc_tolerance" / "mc_tolerance.json"
        sim_dir = p.reports / "sim"
        profile = str(args.get("profile", "default"))
        report = write_gate_report(
            mp,
            out,
            captured_dir=p.captured,
            sim_dir=sim_dir if sim_dir.is_dir() else None,
            sim_raw_path=sim_raw or (sim_dir / "sim_waveform.csv"),
            operating_point=args.get("operating_point"),
            sim_report_path=sim_dir / "sim_report.json",
            stress_sweep_path=stress_sweep_path,
            monte_carlo_path=mc_path if mc_path.is_file() else None,
            rule_pack_paths=rule_pack_paths,
            sim_profile_path=p.sim_profile,
            profile=profile,
        )
        return report.to_dict()

    if name == "diagnose":
        from benchgate.diagnose import diagnose_project

        p = _paths_for_design(args["design_dir"], args)
        return diagnose_project(
            p.reports,
            captured_dir=p.captured,
            gate_report_path=Path(args["gate_report_path"]) if args.get("gate_report_path") else None,
        )

    if name == "lab_compare_waveforms":
        from benchgate.lab.analyze import compare_waveforms
        from benchgate.bench_compare import load_sim_waveform_csv

        p = _paths_for_design(args["design_dir"], args)
        store = LabDataStore(p.captured)
        bench_wf = store.load_waveform(args["session_id"], args.get("bench_channel", "scope_ch1"))
        sim_path = Path(args["sim_csv"])
        if not sim_path.is_absolute():
            sim_path = p.reports / "sim" / sim_path
        sim_wf = load_sim_waveform_csv(sim_path)
        if sim_wf is None:
            raise FileNotFoundError(f"sim waveform not found: {sim_path}")
        cmp = compare_waveforms(bench_wf, sim_wf)
        from benchgate.gate.report import waveform_status_from_comparison

        result = cmp.to_dict()
        result["waveform_status"] = waveform_status_from_comparison(result)
        return result

    if name == "sim_tolerance":
        from benchgate.sim.tolerance import run_tolerance_study

        p = _paths_for_design(args["design_dir"], args)
        mp = Path(args["manifest_path"]) if args.get("manifest_path") else p.manifest
        if not mp.is_absolute():
            mp = resolve_project_path(p.design, mp, p.manifest)
        out_dir = Path(args.get("output_dir", p.reports / "mc_tolerance"))
        if not out_dir.is_absolute():
            out_dir = resolve_project_path(p.design, out_dir, p.reports / "mc_tolerance")
        report = run_tolerance_study(
            p.design,
            mp,
            out_dir,
            blocks_yaml=p.blocks_yaml,
            sim_profile_path=p.sim_profile,
            profile=str(args.get("profile", "charge_pump")),
            n_samples=int(args.get("n_samples", 200)),
            seed=int(args.get("seed", 42)),
            strategy=str(args.get("strategy", "lhs")),
            warmup_ratio=float(args.get("warmup_ratio", 0.25)),
            surrogate_degree=int(args.get("surrogate_degree", 2)),
            sequential_batch=int(args.get("sequential_batch", 25)),
            sequential_ci_width=float(args.get("sequential_ci_width", 5.0)),
            sequential_min_samples=int(args.get("sequential_min_samples", 50)),
            jobs=int(args.get("jobs", 4)),
            sim_tier=args.get("sim_tier"),
            tran_step=args.get("tran_step"),
            tran_stop=args.get("tran_stop"),
            maxstep=args.get("maxstep"),
        )
        return report.to_dict()

    if name == "sim_diagnose":
        p = _paths_for_design(args["design_dir"], args)
        from benchgate.sim.diagnose import diagnose_sim

        return diagnose_sim(p.reports)

    if name == "watch_once":
        p = _paths_for_design(args["design_dir"], args)
        return watch_once(
            p.design,
            manifest_path=p.manifest,
            models_dir=p.models,
            reports_dir=p.reports,
            state_path=p.state,
            sim_profile_path=p.sim_profile,
            profile=str(args.get("profile", "default")),
            subckt_dir=p.subckt,
            global_models_dir=p.global_models,
            blocks_yaml=p.blocks_yaml,
            tmp_dir=p.tmp_root / "pipeline",
            run_pipeline=bool(args.get("run_pipeline", True)),
            run_sim=bool(args.get("run_sim", True)),
            run_gate=bool(args.get("run_gate", True)),
            run_auto_capture=bool(args.get("run_auto_capture", True)),
            auto_capture_dry_run=bool(args.get("auto_capture_dry_run", False)),
            run_tolerance=bool(args.get("run_tolerance", True)),
            tolerance_samples=int(args.get("tolerance_samples", 200)),
            tolerance_strategy=str(args.get("tolerance_strategy", "auto")),
            tolerance_seed=int(args.get("tolerance_seed", 42)),
            tolerance_jobs=int(args.get("tolerance_jobs", 4)),
        )

    if name == "watch_loop":
        from benchgate.watch.loop import watch_loop

        p = _paths_for_design(args["design_dir"], args)
        max_iter = args.get("max_iterations")
        if max_iter == 0:
            max_iter = None
        return watch_loop(
            p.design,
            manifest_path=p.manifest,
            models_dir=p.models,
            reports_dir=p.reports,
            state_path=p.state,
            sim_profile_path=p.sim_profile,
            profile=str(args.get("profile", "default")),
            subckt_dir=p.subckt,
            global_models_dir=p.global_models,
            blocks_yaml=p.blocks_yaml,
            tmp_dir=p.tmp_root / "pipeline",
            run_pipeline=bool(args.get("run_pipeline", True)),
            run_sim=bool(args.get("run_sim", True)),
            run_gate=bool(args.get("run_gate", True)),
            run_auto_capture=bool(args.get("run_auto_capture", True)),
            auto_capture_dry_run=bool(args.get("auto_capture_dry_run", False)),
            interval_s=float(args.get("interval_s", 2.0)),
            debounce_s=float(args.get("debounce_s", 1.0)),
            max_iterations=max_iter,
            run_tolerance=bool(args.get("run_tolerance", True)),
            tolerance_samples=int(args.get("tolerance_samples", 200)),
            tolerance_strategy=str(args.get("tolerance_strategy", "auto")),
            tolerance_seed=int(args.get("tolerance_seed", 42)),
            tolerance_jobs=int(args.get("tolerance_jobs", 4)),
        )

    raise NotImplementedError(name)
