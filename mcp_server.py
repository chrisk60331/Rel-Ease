from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import dotenv
import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.routing import Mount, Route

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

dotenv.load_dotenv()

from rel_ease.mcp_tools import DISPATCH, TOOLS  # noqa: E402

server: Server = Server("rel_ease")


def _as_mcp_tool(schema: dict) -> Tool:
    fn = schema["function"]
    return Tool(
        name=fn["name"],
        description=fn.get("description", ""),
        inputSchema=fn.get("parameters", {"type": "object", "properties": {}}),
    )


_MCP_TOOLS = [_as_mcp_tool(schema) for schema in TOOLS]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return _MCP_TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    handler = DISPATCH.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: handler(**(arguments or {})))
        text = result if isinstance(result, str) else json.dumps(result, default=str)
    except Exception as exc:
        text = json.dumps({"ok": False, "error": str(exc)})
    return [TextContent(type="text", text=text)]


sse = SseServerTransport("/messages/")


async def handle_sse(request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def handle_messages(scope, receive, send):
    await sse.handle_post_message(scope, receive, send)


app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=handle_messages),
    ]
)


if __name__ == "__main__":
    port = int(os.getenv("MCP_PORT", "8072"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
