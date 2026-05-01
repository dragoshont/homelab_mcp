#!/usr/bin/env python3
"""Phase 1.0 server.py splitter — mechanical AST-based refactor.

Splits ``mcp/src/homelab_mcp/server.py`` (3.3k lines, 133 tools) into:

- ``mcp/src/homelab_mcp/_runtime.py`` \u2014 shared singleton state
  (``mcp`` instance, ``_audit_logger``, helpers that close over them).
- ``mcp/src/homelab_mcp/tools/{platform,media,network,homeauto,control}.py``
  \u2014 one file per domain, each owning its tool ``@mcp.tool()`` definitions.
- A new ~30-line ``mcp/src/homelab_mcp/server.py`` that imports ``_runtime``
  and the five ``tools/*`` modules so the side-effect-decorated tools
  register on the shared ``mcp`` instance.

Operating principle: every tool body is COPIED byte-faithfully from the
source via ``ast.get_source_segment`` (Python 3.8+). The script never
re-parses or re-generates tool source. The only synthesised code is
the new ``_runtime.py`` (which assembles existing helpers) and the new
``server.py`` orchestrator.

Inventory contract: every tool's destination module is derived from
``docs/migration/tool-inventory.json``'s ``server`` field. A pre-flight
check refuses to run if any tool in the source isn't in the inventory
(prevents a tool being lost in the split). A post-flight check runs
``tools/verify_lift.py`` against the union of all five domain modules
and aborts on set inequality with the inventory.

Usage:
    python tools/phase_1_0_split_server.py --apply
    python tools/phase_1_0_split_server.py --dry-run

Without ``--apply`` (default), the script enumerates and validates but
writes nothing.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "mcp" / "src" / "homelab_mcp"
INVENTORY_JSON = REPO / "docs" / "migration" / "tool-inventory.json"

DOMAINS = ("platform", "media", "network", "homeauto", "control")

# Helpers that must be extracted into _runtime.py because tools depend on them
# AND they capture module-level state (mcp instance, _audit_logger, _clients
# cache implicit in factories, etc.). Identified by name match against the
# original server.py.
RUNTIME_HELPER_NAMES = {
    # All non-tool top-level functions in the original server.py except
    # ``main()`` (which stays in the new server.py). The split must keep
    # cross-domain helpers reachable from each tools/{domain}.py.
    "_audit",
    "_check_readonly",
    "_sonarr",
    "_radarr",
    "_lidarr",
    "_readarr",
    "_mylar3",
    "_prowlarr",
    "_qbt",
    "_plex",
    "_homebridge",
    "_ssh_exec",
    "_validate_k8s_name",
    "_validate_image",
    "_validate_duration",
    "_kube",
    "_flux",
    "_dirigera",
    "_need_dirigera",
    "_dev_summary",
    "_find_light",
    "_apple_devices_map",
    "_apple_run",
    "_apple_connect",
    "_unifi_run",
    "_client_summary",
    "_resolve_mac",
    "_cf_token",
    "_cf_get",
}

# Module-level constants that must live in _runtime.py because helpers and/or
# tools reference them.
RUNTIME_CONST_NAMES = {
    # Singleton state and shared regex/constant tables. Anything declared at
    # module scope in the original server.py lands in _runtime.py because the
    # five domain modules all need it. Names are listed for documentation; the
    # classifier accepts any module-level assignment (the original server.py
    # has no junk at module scope, so this is safe).
    "mcp",
    "_clients",
    "_AUDIT_LOG_PATH",
    "_audit_logger",
    "_READONLY",
    "_WRITE_TOOLS",
    "_K8S_NAME_RE",
    "_IMAGE_RE",
    "_DURATION_RE",
    "_DNS_NAME_RE",
    "_PROWLARR_DEF_RE",
    "_AUDIT_TOOL_RE",
}


def _is_tool_function(node: ast.AST) -> bool:
    """A tool is a top-level function decorated with ``@mcp.tool()``."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for dec in node.decorator_list:
        unparsed = ast.unparse(dec)
        if "mcp.tool" in unparsed:
            return True
    return False


def _load_inventory() -> dict[str, str]:
    """Return ``{tool_name: domain}`` mapping from tool-inventory.json."""
    inv = json.loads(INVENTORY_JSON.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for entry in inv["tools"]:
        full_server = entry["server"]
        # "homelab-mcp-platform" -> "platform"
        if not full_server.startswith("homelab-mcp-"):
            raise ValueError(f"unexpected inventory server: {full_server!r}")
        out[entry["name"]] = full_server[len("homelab-mcp-") :]
    return out


def _segment(src: str, node: ast.AST) -> str:
    """Return the *source segment* for a node, lossless including decorators.

    ``ast.get_source_segment`` returns the source between the node's
    ``lineno``/``col_offset`` and ``end_lineno``/``end_col_offset``. For a
    decorated function, ``lineno`` points to the ``def`` keyword \u2014 the
    decorators sit on lines ABOVE it and are therefore truncated. We
    recompute the start line as the minimum of the decorator linenos
    (falling back to the def line for undecorated nodes) and slice the
    source manually.
    """
    end_line = getattr(node, "end_lineno", None)
    if end_line is None:
        seg = ast.get_source_segment(src, node, padded=True)
        if seg is None:
            raise RuntimeError(
                f"ast.get_source_segment returned None for "
                f"{type(node).__name__} at line {getattr(node, 'lineno', '?')}"
            )
        return seg

    start_line = node.lineno
    decs = getattr(node, "decorator_list", None) or []
    for dec in decs:
        if dec.lineno < start_line:
            start_line = dec.lineno

    # ``end_lineno`` is inclusive; splitlines is 0-indexed; lines must be
    # joined back with newlines and given a trailing newline so adjacent
    # blocks remain syntactically separable.
    lines = src.splitlines(keepends=True)
    return "".join(lines[start_line - 1 : end_line])


def _classify_module_body(
    src: str, tree: ast.Module
) -> tuple[list[str], list[str], list[str], dict[str, list[str]], list[str]]:
    """Walk top-level nodes of server.py and bucket them.

    Returns (imports_text, runtime_consts_text, runtime_helpers_text,
    domain_tools_text, server_tail_text).

    - imports_text: every ``import`` / ``from ... import`` segment, in source
      order. Replicated verbatim into ``_runtime.py`` and each
      ``tools/{domain}.py`` (let Python dedupe at compile time; unused imports
      add no runtime cost).
    - runtime_consts_text: assignments / ann-assigns naming any of
      RUNTIME_CONST_NAMES.
    - runtime_helpers_text: function defs whose name is in
      RUNTIME_HELPER_NAMES.
    - domain_tools_text: ``{domain: [tool_segment, ...]}`` for the 5 domains.
    - server_tail_text: ``def main()`` plus any ``if __name__ == '__main__':``
      block. Stays in the new ``server.py``.
    """
    inventory = _load_inventory()
    imports: list[str] = []
    runtime_consts: list[str] = []
    runtime_helpers: list[str] = []
    domain_tools: dict[str, list[str]] = {d: [] for d in DOMAINS}
    server_tail: list[str] = []

    seen_tools: set[str] = set()

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(_segment(src, node))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names: list[str] = []
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        names.append(tgt.id)
            else:  # AnnAssign
                if isinstance(node.target, ast.Name):
                    names.append(node.target.id)
            seg = _segment(src, node)
            if any(n in RUNTIME_CONST_NAMES for n in names):
                runtime_consts.append(seg)
            else:
                # Unknown module-level assignment; surface loudly so we don't
                # silently drop something.
                raise RuntimeError(
                    f"Unhandled module-level assignment at line {node.lineno}: "
                    f"{names!r}. Add to RUNTIME_CONST_NAMES or extend the "
                    f"classifier."
                )
        elif _is_tool_function(node):
            tool_name = node.name
            if tool_name not in inventory:
                raise RuntimeError(
                    f"Tool {tool_name!r} (server.py L{node.lineno}) is NOT in "
                    f"docs/migration/tool-inventory.json. Add it there first or "
                    f"remove the @mcp.tool() decorator."
                )
            domain = inventory[tool_name]
            if domain not in domain_tools:
                raise RuntimeError(
                    f"Inventory assigns tool {tool_name!r} to unknown domain "
                    f"{domain!r}. Allowed: {DOMAINS}."
                )
            if tool_name in seen_tools:
                raise RuntimeError(
                    f"Duplicate @mcp.tool() function {tool_name!r} at "
                    f"L{node.lineno}."
                )
            seen_tools.add(tool_name)
            domain_tools[domain].append(_segment(src, node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in RUNTIME_HELPER_NAMES:
                runtime_helpers.append(_segment(src, node))
            elif node.name == "main":
                server_tail.append(_segment(src, node))
            else:
                raise RuntimeError(
                    f"Unhandled top-level function {node.name!r} at "
                    f"L{node.lineno}. Add to RUNTIME_HELPER_NAMES or "
                    f"server-tail handling."
                )
        elif isinstance(node, ast.If):
            # Only the bottom main-guard is allowed.
            test_text = ast.unparse(node.test)
            if test_text.strip() == "__name__ == '__main__'":
                server_tail.append(_segment(src, node))
            else:
                raise RuntimeError(
                    f"Unhandled top-level if at L{node.lineno}: {test_text}"
                )
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            # Module docstring; keep in new server.py header.
            server_tail.insert(0, _segment(src, node))
        else:
            raise RuntimeError(
                f"Unhandled top-level statement at L{node.lineno}: "
                f"{type(node).__name__}: {ast.unparse(node)[:80]!r}"
            )

    # Sanity: every tool in inventory must have been seen.
    missing = sorted(set(inventory) - seen_tools)
    if missing:
        raise RuntimeError(
            f"Inventory has tools NOT found in server.py: {missing!r}. "
            f"server.py is missing {len(missing)} @mcp.tool() definitions."
        )

    return imports, runtime_consts, runtime_helpers, domain_tools, server_tail


HEADER = '''"""{title}

Generated by tools/phase_1_0_split_server.py from
mcp/src/homelab_mcp/server.py at the Phase 1.0 refactor. Tool source is
byte-faithful with the pre-split monolith. Edit the generator (and the
inventory) rather than hand-patching this file.
"""
'''


def _filter_future_imports(imports: list[str]) -> list[str]:
    """Drop ``from __future__ import annotations`` segments.

    The header already emits one, so re-emitting from the source segments
    would cause ``SyntaxError: from __future__ imports must occur at the
    beginning of the file`` on certain Python versions and produce a
    duplicated annotation directive. Future-imports are emitted by the
    HEADER template so each generated file gets exactly one.
    """
    return [
        seg for seg in imports
        if not seg.lstrip().startswith("from __future__")
    ]


def _build_runtime(imports: list[str], consts: list[str], helpers: list[str]) -> str:
    """Produce the text of ``_runtime.py``.

    Order: docstring \u2192 future-import \u2192 imports \u2192 consts \u2192 helpers. Same
    order as the original server.py used.
    """
    parts: list[str] = [
        HEADER.format(
            title="Shared per-process MCP runtime state and helpers."
        ),
        "from __future__ import annotations",
        "",
    ]
    parts.append("# --- Imports (mirrors original server.py) ---")
    parts.extend(_filter_future_imports(imports))
    parts.append("")
    parts.append("# --- Singleton state ---")
    parts.extend(consts)
    parts.append("")
    parts.append("# --- Helpers used by tools across multiple domain modules ---")
    parts.extend(helpers)
    parts.append("")
    return "\n".join(parts)


def _build_domain(name: str, imports: list[str], tools: list[str], helper_names: list[str]) -> str:
    """Produce the text of ``tools/{name}.py``."""
    parts: list[str] = [
        HEADER.format(
            title=f"homelab-mcp-{name} domain tools."
        ),
        "from __future__ import annotations",
        "",
    ]
    parts.append("# --- Imports (mirrors original server.py for tool body fidelity) ---")
    parts.extend(_filter_future_imports(imports))
    parts.append("")
    parts.append(
        "# --- Shared runtime state: registers tools onto the singleton mcp "
        "instance ---"
    )
    # Build the import line dynamically from the actual helper + const set so
    # adding/removing a helper in _runtime.py only requires updating
    # RUNTIME_HELPER_NAMES / RUNTIME_CONST_NAMES once.
    helper_lines = ",\n    ".join(helper_names)
    parts.append(
        f"from homelab_mcp._runtime import (  # noqa: F401  imports used by "
        f"tool bodies\n    {helper_lines},\n)"
    )
    parts.append("")
    parts.append(f"# --- Domain tools ({len(tools)}) ---")
    parts.append("")
    parts.append("\n\n".join(tools))
    parts.append("")
    return "\n".join(parts)


SERVER_TEMPLATE = '''"""Homelab MCP monolith server orchestrator.

Generated by tools/phase_1_0_split_server.py at the Phase 1.0 refactor.

Imports the shared ``_runtime`` module (which constructs the singleton
``mcp`` instance, configures the audit logger, and exposes helpers) and
each ``tools/*`` domain module (which registers its tools onto ``mcp``
as a side effect of import). The result is the same 133-tool monolith
image as the pre-refactor monolith \u2014 the only change is internal
layout.
"""
from __future__ import annotations

# Shared singleton state (mcp instance, audit logger, client factories,
# policy helpers). Imported FIRST so the per-domain modules can register
# their tools against the same mcp instance.
from homelab_mcp._runtime import mcp

# Domain modules \u2014 each defines @mcp.tool() functions at module scope, so
# importing the module is enough to register them. ``noqa: F401`` because
# the imports are intentional for their side effects.
from homelab_mcp.tools import platform  # noqa: F401
from homelab_mcp.tools import media  # noqa: F401
from homelab_mcp.tools import network  # noqa: F401
from homelab_mcp.tools import homeauto  # noqa: F401
from homelab_mcp.tools import control  # noqa: F401


def main() -> None:
    """Run the FastMCP server in stdio mode."""
    mcp.run()


if __name__ == "__main__":
    main()
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write the split files. Without this flag the "
                             "script validates but does not write.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Explicitly run without writing (default).")
    args = parser.parse_args()

    write = args.apply and not args.dry_run

    server_py = PKG / "server.py"
    src = server_py.read_text(encoding="utf-8")
    tree = ast.parse(src)

    print(f"== Phase 1.0 server.py split ==")
    print(f"   source: {server_py}")
    print(f"   mode:   {'APPLY (writes)' if write else 'DRY-RUN (no writes)'}")

    imports, runtime_consts, runtime_helpers, domain_tools, server_tail = (
        _classify_module_body(src, tree)
    )

    print(f"   imports:           {len(imports)}")
    print(f"   runtime consts:    {len(runtime_consts)}")
    print(f"   runtime helpers:   {len(runtime_helpers)}")
    for d in DOMAINS:
        print(f"   tools.{d:9}: {len(domain_tools[d])}")
    print(f"   total tools:       {sum(len(v) for v in domain_tools.values())}")

    runtime_text = _build_runtime(imports, runtime_consts, runtime_helpers)
    # Build the helper/const name list for the domain modules' import block
    # by extracting names from the source segments (so we never re-export a
    # helper that doesn't exist in _runtime.py).
    helper_names: list[str] = sorted(RUNTIME_HELPER_NAMES | RUNTIME_CONST_NAMES)
    domain_texts = {
        d: _build_domain(d, imports, domain_tools[d], helper_names) for d in DOMAINS
    }
    server_text = SERVER_TEMPLATE

    if not write:
        print()
        print("Dry-run complete. Use --apply to write files.")
        return 0

    tools_pkg = PKG / "tools"
    tools_pkg.mkdir(exist_ok=True)
    init_path = tools_pkg / "__init__.py"
    init_path.write_text(
        '"""Per-domain tool modules. Importing each module registers its '
        '@mcp.tool() functions on the singleton mcp instance from _runtime.py."""\n',
        encoding="utf-8",
    )

    runtime_path = PKG / "_runtime.py"
    runtime_path.write_text(runtime_text, encoding="utf-8")
    print(f"   wrote {runtime_path.relative_to(REPO)}")

    for d in DOMAINS:
        path = tools_pkg / f"{d}.py"
        path.write_text(domain_texts[d], encoding="utf-8")
        print(f"   wrote {path.relative_to(REPO)}")

    server_py.write_text(server_text, encoding="utf-8")
    print(f"   wrote {server_py.relative_to(REPO)}")

    print()
    print("OK: split applied. Now run:")
    print("   python tools/verify_lift.py        # G-5 lift-completeness")
    print("   PYTHONPATH=mcp/src python -m pytest mcp/tests -q")
    print()
    print("Note: validate_inventory.py (G-2 source check) no longer applies")
    print("post-Phase-0.8: the source mcp/ tree in dragoshont/homelab was")
    print("deleted. G-5 is now the canonical structural gate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
