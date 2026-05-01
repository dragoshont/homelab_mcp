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

# --- Bundle (multi-domain) entry point ----------------------------------
#
# Phase 1.6: the bundle entry runs ``run_bundle(*domains)`` against an
# explicit domain list resolved from (in priority order):
#
#   1. ``HOMELAB_MCP_BUNDLE_DOMAINS`` env (CSV, e.g. "platform,media").
#      Set this in K8s/compose/systemd to drive bundle composition
#      declaratively.
#   2. A YAML config file at ``HOMELAB_MCP_BUNDLE_CONFIG`` with a
#      top-level ``servers:`` list (subset of SUPPORTED_DOMAINS).
#   3. Fallback: all five domains (drop-in for the pre-Phase-1.0
#      ``homelab-mcp`` monolith console script behaviour).
#
# The CSV env wins over the YAML config so an operator can override
# the baked-in YAML at runtime without rebuilding an image.
#
# Trust boundary: ``control`` is included only if the operator
# explicitly lists it (CSV or YAML). The fallback "all five" preserves
# pre-Phase-1.0 behaviour, but production deployments should NEVER
# rely on the fallback for a control-bearing endpoint \u2014 always be
# explicit, so a typo doesn't accidentally enable mutating tools.

import os as _os


def _read_yaml_servers(path: str) -> list[str] | None:
    """Read a list[str] from ``servers:`` in the YAML config at *path*.

    Returns None if the file doesn't exist; raises on malformed YAML or
    missing/typed-incorrectly ``servers`` key.

    Imports yaml lazily so the rest of the package doesn't hard-depend
    on PyYAML \u2014 the bundle entry point is the only consumer.
    """
    if not _os.path.isfile(path):
        return None
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - install-time concern
        raise RuntimeError(
            f"HOMELAB_MCP_BUNDLE_CONFIG is set ({path}) but PyYAML is not "
            f"installed; install with ``pip install homelab-mcp[bundle]`` or "
            f"set HOMELAB_MCP_BUNDLE_DOMAINS as a CSV instead."
        ) from exc
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    servers = data.get("servers")
    if not isinstance(servers, list):
        raise RuntimeError(
            f"HOMELAB_MCP_BUNDLE_CONFIG {path!r}: top-level ``servers:`` "
            f"must be a list of domain names; got {type(servers).__name__}"
        )
    out: list[str] = []
    for entry in servers:
        if not isinstance(entry, str):
            raise RuntimeError(
                f"HOMELAB_MCP_BUNDLE_CONFIG {path!r}: every entry under "
                f"``servers:`` must be a string; got {type(entry).__name__}"
            )
        out.append(entry)
    return out


def _resolve_bundle_domains() -> tuple[str, ...]:
    """Return the ordered tuple of domains the bundle should run."""
    csv = _os.environ.get("HOMELAB_MCP_BUNDLE_DOMAINS", "").strip()
    if csv:
        domains = tuple(d.strip() for d in csv.split(",") if d.strip())
        if not domains:
            raise RuntimeError(
                "HOMELAB_MCP_BUNDLE_DOMAINS is set but parses to an empty "
                "list. Provide a CSV like 'platform,media' or unset the "
                "variable to fall back to all five."
            )
        return domains
    cfg = _os.environ.get("HOMELAB_MCP_BUNDLE_CONFIG", "").strip()
    if cfg:
        from_yaml = _read_yaml_servers(cfg)
        if from_yaml is None:
            raise RuntimeError(
                f"HOMELAB_MCP_BUNDLE_CONFIG points at {cfg!r} which does "
                f"not exist."
            )
        if not from_yaml:
            raise RuntimeError(
                f"HOMELAB_MCP_BUNDLE_CONFIG {cfg!r}: ``servers:`` list is "
                f"empty. Add at least one of {SUPPORTED_DOMAINS}."
            )
        return tuple(from_yaml)
    return SUPPORTED_DOMAINS


def _main_bundle() -> None:
    """Entry point for the ``homelab-mcp-bundle`` console script."""
    domains = _resolve_bundle_domains()
    run_bundle(*domains)
