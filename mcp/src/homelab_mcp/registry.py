"""Tool inventory helpers for compatibility tests and diagnostics."""

from __future__ import annotations

from collections.abc import Iterable


def registered_tool_names(mcp_app: object) -> frozenset[str]:
    """Return registered tool names from the FastMCP app's tool manager."""
    tool_manager = getattr(mcp_app, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None)
    if isinstance(tools, dict):
        return frozenset(str(name) for name in tools)
    if isinstance(tools, Iterable):
        return frozenset(str(name) for name in tools)
    return frozenset()