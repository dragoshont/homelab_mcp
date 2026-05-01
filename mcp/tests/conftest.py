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
        # BUG-010 fix: per-instance state. Class-level mutable headers/text
        # would let one test mutate the shim and contaminate every later
        # Response() in the same run.
        def __init__(self) -> None:
            self.status_code = 200
            self.headers: dict[str, str] = {}
            self.text = "{}"

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

# --- Phase 1.0 split-server facade for tests -----------------------------
#
# Before Phase 1.0, every tool function lived in homelab_mcp.server, and
# tests monkeypatched helpers via setattr(server, "_qbt", mock). After the
# split, tools live in homelab_mcp.tools.{platform,media,network,homeauto,
# control} and helpers live in homelab_mcp._runtime; a single from-import
# binding in each module would defeat the monkeypatch pattern.
#
# This facade preserves the pre-refactor test API by proxying:
#   - getattr(facade, name) reads from _runtime first, then each tools/*
#     module (so facade.qbt_pause finds the tool function and facade._qbt
#     finds the helper).
#   - setattr(facade, name, value) writes to _runtime AND to every tool
#     module that already has 'name' bound at module scope. That way a
#     monkeypatch on facade._qbt propagates into every tool's globals
#     (which is where the tool body's _qbt() call resolves).
#
# Tests get back the exact same ergonomics as the pre-split monolith.
import importlib as _importlib
import types as _types


_RUNTIME_MODNAME = "homelab_mcp._runtime"
_TOOL_MODNAMES = (
    "homelab_mcp.tools.platform",
    "homelab_mcp.tools.media",
    "homelab_mcp.tools.network",
    "homelab_mcp.tools.homeauto",
    "homelab_mcp.tools.control",
)
_FACADE_MODULES = (
    _RUNTIME_MODNAME,
    "homelab_mcp.server",
    *_TOOL_MODNAMES,
)


class _SplitServerFacade(_types.SimpleNamespace):
    """Test-only facade over the post-split package. See module docstring."""

    def __init__(self, runtime, tool_mods):
        # Bypass __setattr__ for our own bookkeeping.
        object.__setattr__(self, "_runtime", runtime)
        object.__setattr__(self, "_tool_mods", tool_mods)

    def __getattr__(self, name):  # called only for missing attrs
        runtime = self.__dict__["_runtime"]
        if hasattr(runtime, name):
            return getattr(runtime, name)
        for mod in self.__dict__["_tool_mods"]:
            if hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(
            f"split-server facade has no attribute {name!r}; checked "
            f"_runtime and {[m.__name__ for m in self.__dict__['_tool_mods']]}"
        )

    def __setattr__(self, name, value):
        if name.startswith("_") and name in ("_runtime", "_tool_mods"):
            object.__setattr__(self, name, value)
            return
        # Mirror onto facade.__dict__ so unittest.mock.patch.object's
        # ``is_local = attr in target.__dict__`` check passes. Without this
        # mirror, patch.object falls into its delete-on-cleanup branch and
        # trips with AttributeError on exit. The mirror is harmless: every
        # later getattr resolves via __getattribute__ from __dict__ first,
        # which then equals the value we already propagated to the
        # underlying modules.
        object.__setattr__(self, name, value)
        runtime = self.__dict__["_runtime"]
        if hasattr(runtime, name):
            setattr(runtime, name, value)
        for mod in self.__dict__["_tool_mods"]:
            if hasattr(mod, name):
                setattr(mod, name, value)

    def __delattr__(self, name):
        # patch.object will not normally call this (because __setattr__
        # mirrors to __dict__, making is_local True). Implemented for
        # completeness so any other test that does ``del facade.x`` still
        # works: drop from __dict__ but leave underlying modules untouched
        # \u2014 we don't know whether the original value should be gone.
        if name in self.__dict__:
            object.__delattr__(self, name)


def reload_server_facade():
    """Drop and reimport every facade-backed module, return a fresh facade.

    Order matters: _runtime must reload first (so any env-driven state like
    _READONLY is recomputed), then server.py, then each tool module (each
    tool module re-imports from _runtime at the top, so reloading them
    after _runtime gives them the fresh bindings).
    """
    import sys as _sys
    for name in _FACADE_MODULES:
        _sys.modules.pop(name, None)
    runtime = _importlib.import_module(_RUNTIME_MODNAME)
    # Importing server.py triggers the side-effect imports of the tool
    # modules. We then re-resolve the tool module references explicitly.
    _importlib.import_module("homelab_mcp.server")
    tool_mods = [_importlib.import_module(n) for n in _TOOL_MODNAMES]
    return _SplitServerFacade(runtime, tool_mods)
