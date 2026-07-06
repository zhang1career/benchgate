"""stdio MCP server exposing benchgate agent tools."""

from __future__ import annotations

import json
from typing import Any

from benchgate.agent.dispatch import dispatch
from benchgate.agent.tools import TOOLS


def _require_mcp():
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError as exc:  # pragma: no cover - optional extra
        raise SystemExit(
            "benchgate MCP server requires the 'agent' extra: pip install 'benchgate[agent]'"
        ) from exc
    return Server, stdio_server, TextContent, Tool


def build_server():
    Server, stdio_server, TextContent, Tool = _require_mcp()
    server = Server("benchgate")

    @server.list_tools()
    async def list_tools_handler() -> list[Any]:
        return [
            Tool(
                name=name,
                description=spec["description"],
                inputSchema=spec["parameters"],
            )
            for name, spec in TOOLS.items()
        ]

    @server.call_tool()
    async def call_tool_handler(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        payload = arguments or {}
        try:
            result = dispatch(name, payload)
            text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 — surface tool errors to MCP client
            text = json.dumps({"error": str(exc), "tool": name}, indent=2)
        return [TextContent(type="text", text=text)]

    return server, stdio_server


def main() -> None:
    import asyncio

    server, stdio_server = build_server()

    async def run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(run())


if __name__ == "__main__":
    main()
