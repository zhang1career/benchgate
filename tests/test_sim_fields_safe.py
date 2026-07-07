"""Tests for KiCad 10-safe Sim.* writer."""

from __future__ import annotations

from pathlib import Path

from benchgate.kicad.sim_fields_safe import apply_sim_fields_safe, read_sim_fields_safe

SCH_SNIPPET = """(kicad_sch (version 20250114)
\t(symbol
\t\t(lib_id "Timer:NE555")
\t\t(property "Reference" "U1"
\t\t\t(at 0 0 0)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "NE555"
\t\t\t(at 0 0 0)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(pin "1" (uuid "a"))
\t)
)
"""


def test_apply_sim_fields_safe(tmp_path: Path) -> None:
    sch = tmp_path / "board.kicad_sch"
    sch.write_text(SCH_SNIPPET, encoding="utf-8")
    updated = apply_sim_fields_safe(
        sch,
        "U1",
        sim_library="/tmp/ne555.lib",
        sim_name="NE555",
        sim_pins="1=GND 2=TRIG",
    )
    assert updated
    text = sch.read_text(encoding="utf-8")
    assert 'Sim.Library" "/tmp/ne555.lib"' in text
    assert 'Sim.Name" "NE555"' in text
    fields = read_sim_fields_safe(sch, "U1")
    assert fields is not None
    assert fields.name == "NE555"
    assert fields.library == "/tmp/ne555.lib"
