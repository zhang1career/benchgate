"""Dispatch agent tool calls to benchgate modules."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchgate.agent.tools import TOOLS
from benchgate.gate.report import write_gate_report
from benchgate.io.manifest import load_manifest, save_manifest
from benchgate.kicad.project import KiCadProject
from benchgate.kicad.spice_fields import apply_model_to_reference
from benchgate.lab.capture import LabSession, capture_and_fit
from benchgate.lab.fit import measured_to_subckt, write_subckt
from benchgate.lab.store import LabDataStore
from benchgate.mapping.engine import apply_measured_model, mapping_status, sync_project
from benchgate.paths import benchgate_paths, resolve_project_path
from benchgate.schemas import ComponentMapping, MappingManifest, ModelProvenance, ModelSource
from benchgate.sim.pipeline import run_project_sim
from benchgate.watch.trigger import watch_once


def _paths_for_design(design_dir: str | Path, args: dict[str, Any]):
    return benchgate_paths(
        design_dir,
        manifest=args.get("manifest_path"),
    )


def _role_overrides(args: dict[str, Any]) -> dict[str, str | None]:
    return {role: args[role] for role in ("scope", "dmm", "awg") if args.get(role)}


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
        from benchgate.providers import register_model
        from benchgate.providers.ltspice import LtspiceModelProvider

        p = _paths_for_design(args["design_dir"], args)
        provider_name = args.get("provider", "ltspice")
        if provider_name != "ltspice":
            raise ValueError(f"unknown model provider: {provider_name!r}")

        source_file = resolve_project_path(p.design, args["source_file"], p.design)
        pins = args["pins"].split() if args.get("pins") else None
        provider = LtspiceModelProvider(
            net_path=source_file,
            sim_name=args["sim_name"],
            pins=pins,
            valid_range=args.get("valid_range") or {},
            notes=args.get("notes"),
        )
        manifest = (
            load_manifest(p.manifest, global_models_dir=p.global_models)
            if p.manifest.exists()
            else MappingManifest()
        )
        entry = manifest.find(args["kicad_key"]) or ComponentMapping(kicad_key=args["kicad_key"])
        if args.get("reference"):
            entry.reference = args["reference"]
        artifact = provider.build(entry, workdir=p.subckt)
        register_model(manifest, entry, artifact)
        save_manifest(manifest, p.manifest, global_models_dir=p.global_models)
        return {
            "kicad_key": entry.kicad_key,
            "provider": provider_name,
            "source": artifact.provenance.source.value,
            "subckt": str(artifact.lib_path),
            "sim_name": artifact.sim_name,
            "sim_pins": artifact.sim_pins,
            "warnings": artifact.provenance.notes,
        }

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
                }
                for e in manifest.entries
            ]
        }

    if name == "lab_capture":
        p = _paths_for_design(args["design_dir"], args)
        bench = _open_bench(p, args)
        store = LabDataStore(p.captured)
        ref = args["component_ref"]
        mpn = args["mpn"]
        kicad_key = args["kicad_key"]
        with LabSession(bench) as session:
            measured, meta = capture_and_fit(
                session,
                store,
                component_ref=ref,
                mpn=mpn,
                kicad_key=kicad_key,
                design=str(p.design),
                tags=args.get("tags"),
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
            measured=measured,
        )
        save_manifest(manifest, p.manifest, global_models_dir=p.global_models)
        return {
            "measured": measured.params,
            "subckt": str(subckt_path),
            "kicad_key": kicad_key,
            "session_id": meta.session_id,
        }

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
        )
        return {
            "success": report.success,
            "report": report.to_dict(),
            "stderr": result.stderr,
        }

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
        report = write_gate_report(
            mp,
            out,
            captured_dir=p.captured,
            sim_raw_path=sim_raw,
            operating_point=args.get("operating_point"),
        )
        return report.to_dict()

    if name == "watch_once":
        p = _paths_for_design(args["design_dir"], args)
        return watch_once(
            p.design,
            manifest_path=p.manifest,
            models_dir=p.models,
            reports_dir=p.reports,
            state_path=p.state,
            sim_profile_path=p.sim_profile,
            subckt_dir=p.subckt,
            global_models_dir=p.global_models,
            run_sim=bool(args.get("run_sim", True)),
        )

    raise NotImplementedError(name)
