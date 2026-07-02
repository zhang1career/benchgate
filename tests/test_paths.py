"""Tests for benchgate path resolution."""

from pathlib import Path

from benchgate.paths import benchgate_home, benchgate_paths, resolve_design, resolve_project_path


def test_benchgate_home_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BENCHGATE_HOME", str(tmp_path / "bg"))
    assert benchgate_home() == (tmp_path / "bg").resolve()


def test_benchgate_paths_anchor_on_design(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BENCHGATE_HOME", str(tmp_path / "home"))
    design = tmp_path / "proj" / "myboard"
    design.mkdir(parents=True)

    p = benchgate_paths(design)
    assert p.design == design.resolve()
    assert p.manifest == design / "models" / "manifest.yaml"
    assert p.lab_config == design / "models" / "lab.yaml"
    assert p.captured == design / "models" / "captured"
    assert p.reports == design / "reports"
    assert p.subckt == tmp_path / "home" / "models" / "subckt"
    assert p.config == tmp_path / "home" / "config"
    assert p.state.parent == tmp_path / "home" / "state"
    assert p.blocks_yaml == design / "models" / "blocks.yaml"
    assert p.blocks_dir == design / "models" / "blocks"


def test_resolve_project_path_relative_to_design(tmp_path: Path) -> None:
    design = tmp_path / "board"
    design.mkdir()
    out = resolve_project_path(design, "reports/sim", design / "reports" / "sim")
    assert out == (design / "reports" / "sim").resolve()


def test_resolve_design_relative_uses_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    resolved = resolve_design("design/foo")
    assert resolved == (tmp_path / "design" / "foo").resolve()
