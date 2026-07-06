"""Tests for VendorModelProvider."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchgate.providers.vendor import VendorModelProvider, inspect_vendor_lib
from benchgate.schemas import ComponentMapping, ModelSource, SpiceModelKind


VENDOR_LIB = """* vendor diode
.model D1N4001 D(Is=18.8n Rs=0.042 N=1.08)
"""


def test_vendor_provider_copies_lib(tmp_path: Path) -> None:
    src = tmp_path / "vendor" / "1N4001.lib"
    src.parent.mkdir()
    src.write_text(VENDOR_LIB, encoding="utf-8")
    workdir = tmp_path / "subckt"
    workdir.mkdir()
    provider = VendorModelProvider(lib_path=src, sim_name="D1N4001")
    art = provider.build(ComponentMapping(kicad_key="D:1N4001"), workdir=workdir)
    assert art.provenance.source == ModelSource.VENDOR
    assert art.sim_name == "D1N4001"
    assert art.lib_path.exists()
    assert art.lib_path.read_text(encoding="utf-8") == VENDOR_LIB


def test_vendor_encrypted_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.lib"
    path.write_bytes(b"\x00encrypted blob")
    with pytest.raises(ValueError, match="encrypted"):
        inspect_vendor_lib(path)


def test_vendor_infer_sim_name(tmp_path: Path) -> None:
    src = tmp_path / "part.lib"
    src.write_text(".subckt MY_PART a b\n.ends\n", encoding="utf-8")
    provider = VendorModelProvider(lib_path=src, sim_name="")
    art = provider.build(
        ComponentMapping(kicad_key="U:1", spice_kind=SpiceModelKind.SUBCKT),
        workdir=tmp_path / "out",
    )
    assert art.sim_name == "MY_PART"
