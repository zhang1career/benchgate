"""Tests for ERC report parsing."""

from __future__ import annotations

from pathlib import Path

from benchgate.kicad.erc import parse_erc_report


def test_parse_erc_report(tmp_path: Path):
    text = """ERC report (2026-08-03T20:09:11, Encoding UTF8)
Report includes: Errors, Warnings

***** Sheet /
[power_pin_not_driven]: Input Power pin not driven by any Output Power pins
    ; error
    @(218.44 mm, 125.73 mm): Symbol U4 Pin 5 [EN, Input, Line]
[pin_to_pin]: Pins of type Unspecified and Power input are connected
    ; warning
    @(228.60 mm, 142.24 mm): Symbol U4 Pin 9 [PAD, Unspecified, Line]

 ** ERC messages: 7  Errors 1  Warnings 1
"""
    path = tmp_path / "board.erc.rpt"
    path.write_text(text, encoding="utf-8")
    summary = parse_erc_report(path)
    assert summary.errors == 1
    assert summary.warnings == 1
    assert len(summary.items) == 2
    assert summary.items[0].code == "power_pin_not_driven"
