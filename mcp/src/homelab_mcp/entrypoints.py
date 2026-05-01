"""Single-domain entry-point factory.

Phase 1.1 introduces per-domain MCP server images
(``homelab-mcp-network``, ``-media``, ``-platform``, ``-homeauto``,
``-control``). Each domain image is the same Python package as the
monolith but with an entry point that imports a single ``tools/{domain}``
module rather than all five.

Usage from ``homelab-mcp-{domain}`` console scripts (defined in
pyproject.toml):

    from homelab_mcp.entrypoints import run_domain
    def main() -> None: run_domain("network")

The :func:`run_domain` function:

1. Imports ``homelab_mcp._runtime`` (which constructs the singleton
   ``mcp`` FastMCP instance).
2. Imports the requested ``homelab_mcp.tools.{domain}`` module, whose
   side-effect ``@mcp.tool()`` decorators register that domain's tools
   on the singleton.
3. Runs ``mcp.run()``.

Importantly, **no other tool module is imported**. The resulting server
exposes exactly the tools belonging to the requested domain (e.g. 7 for
network, 30 for media, etc.) \u2014 verified against
``docs/migration/tool-inventory.json`` by the per-domain release smoke
test in CI.

Trust boundary: ``run_domain("control")`` is the only entry that
exposes mutating tools. Per-domain images that aren't ``control`` carry
no write tools by construction (their tools/{domain}.py contains only
read-only @mcp.tool functions per the inventory).
"""
from __future__ import annotations

import importlib

# These names mirror ``DOMAINS`` in ``tools/phase_1_0_split_server.py`` and
# ``server`` field values in ``docs/migration/tool-inventory.json``. The
# whitelist refuses arbitrary domain strings so a typo at deploy time
# doesn't import an unintended attribute lookup.
SUPPORTED_DOMAINS = ("platform", "media", "network", "homeauto", "control")


def run_domain(domain: str) -> None:
    """Register a single domain's tools and run the MCP server.

    Raises:
        ValueError: if ``domain`` is not in :data:`SUPPORTED_DOMAINS`.
    """
    if domain not in SUPPORTED_DOMAINS:
        raise ValueError(
            f"unknown domain {domain!r}; expected one of {SUPPORTED_DOMAINS}"
        )
    runtime = importlib.import_module("homelab_mcp._runtime")
    importlib.import_module(f"homelab_mcp.tools.{domain}")
    runtime.mcp.run()


def run_bundle(*domains: str) -> None:
    """Register multiple domains and run a single MCP server (Phase 1.6).

    With no arguments, registers all five domains \u2014 equivalent to the
    pre-Phase-1.0 monolith ``homelab-mcp`` console script.

    Raises:
        ValueError: on any unknown domain name.
    """
    selected = domains or SUPPORTED_DOMAINS
    unknown = [d for d in selected if d not in SUPPORTED_DOMAINS]
    if unknown:
        raise ValueError(
            f"unknown domain(s) {unknown!r}; expected subset of "
            f"{SUPPORTED_DOMAINS}"
        )
    runtime = importlib.import_module("homelab_mcp._runtime")
    for d in selected:
        importlib.import_module(f"homelab_mcp.tools.{d}")
    runtime.mcp.run()

# --- Per-domain console-script entry points -----------------------------
# Each shim is a no-arg callable that ``[project.scripts]`` can reference;
# the ``run_domain`` indirection keeps the actual logic in a single place.

def _main_platform() -> None:
    run_domain("platform")


def _main_media() -> None:
    run_domain("media")


def _main_network() -> None:
    run_domain("network")


def _main_homeauto() -> None:
    run_domain("homeauto")


def _main_control() -> None:
    run_domain("control")
