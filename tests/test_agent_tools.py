"""The MCP tool registry has to match what dispatch actually implements."""

from __future__ import annotations

from benchgate.agent.tools import TOOLS, _METRIC_NAMES
from benchgate.sim.analysis import METRIC_NAMES


def test_metric_names_in_schema_match_the_implementation():
    """tools.py spells the metric list out to keep numpy off the MCP handshake."""
    advertised = {name.strip() for name in _METRIC_NAMES.split(",")}
    assert advertised == set(METRIC_NAMES)


def test_sweep_tools_are_registered_and_documented():
    for name in ("sim_sweep", "sim_block_sweep"):
        tool = TOOLS[name]
        assert tool["description"]
        props = tool["parameters"]["properties"]
        assert "metrics" in props
        for required in tool["parameters"]["required"]:
            assert required in props, f"{name}: required {required!r} is not a declared property"


def test_block_sweep_needs_no_profile_or_manifest():
    """The point of the block sweep is that it works without a KiCad project."""
    props = TOOLS["sim_block_sweep"]["parameters"]["properties"]
    assert "profile" not in props
    assert "manifest_path" not in props
    assert set(TOOLS["sim_block_sweep"]["parameters"]["required"]) == {
        "design_dir",
        "netlist",
        "metrics",
    }


def test_every_declared_tool_is_dispatchable():
    from benchgate.agent import dispatch as dispatch_mod

    source = dispatch_mod.__file__
    assert source
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    missing = [name for name in TOOLS if f'== "{name}"' not in text]
    assert not missing, f"declared but not handled in dispatch: {missing}"
