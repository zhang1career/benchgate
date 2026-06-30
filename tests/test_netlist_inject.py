"""Tests for profile injection placement."""

from benchgate.sim.netlist import inject_models
from benchgate.schemas import MappingManifest


def test_profile_injected_before_end() -> None:
    netlist = "* test\nR1 a b 1k\n.end\n"
    manifest = MappingManifest()
    profile = "V1 a 0 DC 1\n.control\ntran 1u 1m\nrun\n.endc\n"
    out = inject_models(
        netlist,
        manifest,
        sim_profile_path=None,
        profile="default",
    )
    # Manually inject profile text by calling internal path
    from benchgate.sim.netlist import load_sim_profile
    from pathlib import Path
    import tempfile

    cfg = Path(tempfile.gettempdir()) / "test_sim_profile.yaml"
    cfg.write_text("pwm:\n  directives:\n    - 'V1 a 0 DC 1'\n    - '.control'\n    - 'tran 1u 1m'\n    - 'run'\n    - '.endc'\n")
    out = inject_models(netlist, manifest, sim_profile_path=cfg, profile="pwm")
    assert "V1 a 0 DC 1" in out
    assert out.index("V1 a 0 DC 1") < out.lower().index(".end")
