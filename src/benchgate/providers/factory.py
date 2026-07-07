"""Resolve model providers by name."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from benchgate.providers.bench import BenchModelProvider
from benchgate.providers.datasheet import DatasheetModelProvider
from benchgate.providers.ltspice import LtspiceModelProvider
from benchgate.providers.vendor import VendorModelProvider
from benchgate.schemas import ComponentMapping, MeasuredParams


def create_model_provider(
    provider_name: str,
    *,
    entry: ComponentMapping,
    source_file: Path | None = None,
    sim_name: str | None = None,
    pins: list[str] | None = None,
    mpn: str | None = None,
    lib_path: Path | None = None,
    valid_range: dict[str, Any] | None = None,
    metrics: dict[str, float] | None = None,
    notes: str | None = None,
    measured: MeasuredParams | None = None,
    sim_pins: str | None = None,
):
    name = provider_name.lower()
    if name == "ltspice":
        if not source_file or not sim_name:
            raise ValueError("ltspice provider requires source_file and sim_name")
        return LtspiceModelProvider(
            net_path=source_file,
            sim_name=sim_name,
            pins=pins,
            valid_range=valid_range or {},
            metrics=metrics or {},
            notes=notes,
        )
    if name == "datasheet":
        part = mpn or (entry.metadata or {}).get("value") or ""
        if not part:
            raise ValueError("datasheet provider requires --mpn or manifest entry value")
        return DatasheetModelProvider(
            mpn=part,
            valid_range=valid_range or {},
            notes=notes,
        )
    if name == "bench":
        if not lib_path or not sim_name:
            raise ValueError("bench provider requires lib_path and sim_name")
        return BenchModelProvider(
            lib_path=lib_path,
            sim_name=sim_name,
            sim_pins=sim_pins,
            measured=measured,
            metrics=metrics or {},
            valid_range=valid_range or {},
            notes=notes,
        )
    if name == "vendor":
        if not lib_path:
            raise ValueError("vendor provider requires lib_path (--lib)")
        return VendorModelProvider(
            lib_path=lib_path,
            sim_name=sim_name or "",
            sim_pins=sim_pins,
            valid_range=valid_range or {},
            notes=notes,
        )
    raise ValueError(f"unknown model provider: {provider_name!r}")
