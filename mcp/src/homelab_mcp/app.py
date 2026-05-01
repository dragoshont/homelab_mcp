"""FastMCP application construction for the homelab MCP server."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def create_mcp() -> FastMCP:
    """Create the single FastMCP application used by the server."""
    return FastMCP("homelab")