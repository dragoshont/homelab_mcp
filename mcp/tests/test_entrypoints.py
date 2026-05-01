"""Tests for homelab_mcp.entrypoints (Phase 1.1 per-domain entry points).

These tests verify the public surface of run_domain / run_bundle and the
five console-script shims registered in pyproject.toml. They do NOT
actually start the MCP server (mcp.run() is monkey-patched out) so they
can run in CI without a real stdio peer.
"""
from __future__ import annotations

import importlib
import sys

import pytest


SUPPORTED = ("platform", "media", "network", "homeauto", "control")


def _purge_homelab_mcp_modules():
    """Drop all cached homelab_mcp.* modules so the next import is fresh."""
    for name in list(sys.modules):
        if name.startswith("homelab_mcp"):
            sys.modules.pop(name, None)


@pytest.fixture
def fresh_entrypoints(monkeypatch):
    """Yield a fresh ``homelab_mcp.entrypoints`` module with mcp.run stubbed."""
    _purge_homelab_mcp_modules()
    ep = importlib.import_module("homelab_mcp.entrypoints")
    runtime = importlib.import_module("homelab_mcp._runtime")
    calls: list[None] = []
    monkeypatch.setattr(runtime.mcp, "run", lambda: calls.append(None))
    yield ep, calls
    _purge_homelab_mcp_modules()


@pytest.mark.parametrize("domain", SUPPORTED)
def test_run_domain_imports_only_one_tool_module(fresh_entrypoints, domain):
    """Phase 1.1 contract: run_domain('X') imports exactly tools/X."""
    ep, calls = fresh_entrypoints
    ep.run_domain(domain)
    assert calls == [None], "mcp.run() must be invoked exactly once"
    # The requested domain module is loaded; the others must NOT be.
    loaded = {d for d in SUPPORTED if f"homelab_mcp.tools.{d}" in sys.modules}
    assert loaded == {domain}, (
        f"run_domain({domain!r}) loaded {loaded}, expected only {{{domain!r}}}"
    )


def test_run_domain_rejects_unknown(fresh_entrypoints):
    ep, _ = fresh_entrypoints
    with pytest.raises(ValueError, match="unknown domain"):
        ep.run_domain("not-a-real-domain")


def test_run_bundle_with_no_args_loads_all_five(fresh_entrypoints):
    ep, calls = fresh_entrypoints
    ep.run_bundle()
    assert calls == [None]
    loaded = {d for d in SUPPORTED if f"homelab_mcp.tools.{d}" in sys.modules}
    assert loaded == set(SUPPORTED)


def test_run_bundle_with_subset_loads_only_requested(fresh_entrypoints):
    ep, _ = fresh_entrypoints
    ep.run_bundle("network", "homeauto")
    loaded = {d for d in SUPPORTED if f"homelab_mcp.tools.{d}" in sys.modules}
    assert loaded == {"network", "homeauto"}


def test_run_bundle_rejects_unknown_in_subset(fresh_entrypoints):
    ep, _ = fresh_entrypoints
    with pytest.raises(ValueError, match="unknown domain"):
        ep.run_bundle("network", "bogus")


@pytest.mark.parametrize("domain", SUPPORTED)
def test_main_shim_resolves_to_run_domain(fresh_entrypoints, domain):
    """Each [project.scripts] shim must call run_domain(<its-domain>).

    This is the contract pyproject.toml depends on; if the shim is renamed
    or accidentally bound to the wrong domain, the per-domain image would
    expose the wrong tool surface.
    """
    ep, calls = fresh_entrypoints
    shim_name = f"_main_{domain}"
    assert hasattr(ep, shim_name), f"entrypoints module missing {shim_name}"
    getattr(ep, shim_name)()
    assert calls == [None]
    loaded = {d for d in SUPPORTED if f"homelab_mcp.tools.{d}" in sys.modules}
    assert loaded == {domain}, (
        f"shim {shim_name} loaded {loaded}, expected only {{{domain!r}}}"
    )