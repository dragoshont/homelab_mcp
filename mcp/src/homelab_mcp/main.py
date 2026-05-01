"""Process entrypoint wrapper for the homelab MCP server."""

from __future__ import annotations


def main() -> None:
    """Run the compatibility server entrypoint."""
    from homelab_mcp.server import main as server_main

    server_main()