"""Test-only dependency shims for local Python versions without MCP wheels."""

from __future__ import annotations

import importlib.util
import sys
import types
from types import SimpleNamespace


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


if not _module_available("mcp.server.fastmcp"):
    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:
        def __init__(self, name: str):
            self.name = name
            self._tool_manager = SimpleNamespace(_tools={})

        def tool(self):
            def decorator(func):
                self._tool_manager._tools[func.__name__] = func
                return func

            return decorator

        def run(self):
            return None

    fastmcp_module.FastMCP = FastMCP
    server_module.fastmcp = fastmcp_module
    mcp_module.server = server_module
    sys.modules.setdefault("mcp", mcp_module)
    sys.modules.setdefault("mcp.server", server_module)
    sys.modules.setdefault("mcp.server.fastmcp", fastmcp_module)


if not _module_available("httpx"):
    httpx_module = types.ModuleType("httpx")

    class Response:
        status_code = 200
        headers: dict[str, str] = {}
        text = "{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return Response()

        def post(self, *args, **kwargs):
            return Response()

        def put(self, *args, **kwargs):
            return Response()

    httpx_module.Client = Client
    sys.modules.setdefault("httpx", httpx_module)