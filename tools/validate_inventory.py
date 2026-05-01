#!/usr/bin/env python3
"""Phase 0 inventory validator (design.md ss9.1, 12).

Validates docs/migration/tool-inventory.json against the source repo at the
pinned commit. Run from this repo's root.

Usage:
    python tools/validate_inventory.py [--source-repo PATH] [--commit SHA]

Exits 0 on success. Exits non-zero with a diff on any failure.

Implements MUST PASS test cases T1-T7 and rejects MUST FAIL cases T8-T12
from design.md s12.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
INVENTORY_PATH = REPO_ROOT / "docs" / "migration" / "tool-inventory.json"

# Map from source-prefix to target server. Mutating tools always go to
# homelab-mcp-control regardless of prefix; this map is consulted only for
# read-only tools.
PREFIX_TO_SERVER_RO: dict[str, str] = {
    # platform
    "kube": "homelab-mcp-platform",
    "host": "homelab-mcp-platform",
    "ansible": "homelab-mcp-platform",
    "backup": "homelab-mcp-platform",
    "image": "homelab-mcp-platform",
    "gitops": "homelab-mcp-platform",
    "flux": "homelab-mcp-platform",
    "audit": "homelab-mcp-platform",
    "cert": "homelab-mcp-platform",
    "dns": "homelab-mcp-platform",
    "homelab": "homelab-mcp-platform",
    "ingress": "homelab-mcp-platform",
    "netdata": "homelab-mcp-platform",
    # media
    "sonarr": "homelab-mcp-media",
    "radarr": "homelab-mcp-media",
    "lidarr": "homelab-mcp-media",
    "readarr": "homelab-mcp-media",
    "mylar3": "homelab-mcp-media",
    "prowlarr": "homelab-mcp-media",
    "qbt": "homelab-mcp-media",
    "plex": "homelab-mcp-media",
    "media": "homelab-mcp-media",
    "cf": "homelab-mcp-media",
    # network
    "unifi": "homelab-mcp-network",
    # homeauto
    "dirigera": "homelab-mcp-homeauto",
    "homebridge": "homelab-mcp-homeauto",
    "scrypted": "homelab-mcp-homeauto",
    "apple": "homelab-mcp-homeauto",
}

CONTROL_SERVER = "homelab-mcp-control"
ALL_SERVERS = {
    "homelab-mcp-platform",
    "homelab-mcp-media",
    "homelab-mcp-network",
    "homelab-mcp-homeauto",
    CONTROL_SERVER,
}

EXPECTED_TOTALS = {"tools": 133, "writes": 29, "readonly": 104}

# AS-14: recognised decorator forms. The decorator MUST be `mcp.tool` (or
# bare `mcp.tool` attribute) where `mcp` is the FastMCP app variable. A
# decorator like `@cli.tool` or `@something_else.tool` is NOT an MCP tool
# and must not be treated as one (verify finding ADV-001 critical).
MCP_APP_NAMES = {"mcp"}
RECOGNISED_DECORATOR_ATTRS = {"tool"}


def _is_mcp_tool_decorator(dec: ast.expr) -> bool:
    """Return True iff dec is `@mcp.tool` or `@mcp.tool(...)`.

    Rejects bare names (`@tool`) and decorators rooted in other namespaces
    (`@cli.tool`, `@app.tool`).
    """
    target = dec.func if isinstance(dec, ast.Call) else dec
    if not isinstance(target, ast.Attribute):
        return False
    if target.attr not in RECOGNISED_DECORATOR_ATTRS:
        return False
    base = target.value
    if isinstance(base, ast.Name) and base.id in MCP_APP_NAMES:
        return True
    return False


class ValidationError(Exception):
    pass


def scan_server_tools(server_py: Path) -> list[str]:
    """AST-scan server.py for @mcp.tool decorators, strict mode."""
    src = server_py.read_text(encoding="utf-8")
    module = ast.parse(src)
    tools: list[str] = []
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.decorator_list:
            continue
        # Any decorator with attr 'tool' on a top-level function MUST be
        # an mcp.tool decorator. A different namespace (cli.tool, app.tool)
        # is a strict-mode failure: either the source is using a non-MCP
        # decorator that we must learn about, or it's wrong.
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr in RECOGNISED_DECORATOR_ATTRS:
                if not _is_mcp_tool_decorator(dec):
                    base = target.value
                    base_repr = base.id if isinstance(base, ast.Name) else ast.dump(base)
                    raise ValidationError(
                        f"strict-mode: function {node.name!r} decorated with "
                        f"{base_repr}.tool but only {sorted(MCP_APP_NAMES)}.tool is recognised"
                    )
                tools.append(node.name)
                break
    return tools


def _extract_set_constants(seq: ast.expr) -> list[str] | None:
    """Return string constants from a Set/List/Tuple, or None if not such."""
    if isinstance(seq, (ast.Set, ast.List, ast.Tuple)):
        return sorted(e.value for e in seq.elts if isinstance(e, ast.Constant))
    return None


def _extract_write_tools_value(value: ast.expr | None) -> list[str] | None:
    """Pull tool names from any of the supported WRITE_TOOLS RHS forms.

    Supported (verify findings ADV-002 / ADV-008 high):
      - frozenset({"a", "b"}) / set({"a", "b"}) / tuple(["a", "b"])
      - {"a", "b"} (bare set literal)
      - ["a", "b"] (bare list literal)
      - ("a", "b") (bare tuple literal)
    Both ast.Assign and ast.AnnAssign reach here.
    """
    if value is None:
        return None
    # frozenset({...}) / set({...}) / tuple([...]) form.
    if isinstance(value, ast.Call) and value.args:
        names = _extract_set_constants(value.args[0])
        if names is not None:
            return names
    # Bare set/list/tuple literal form.
    return _extract_set_constants(value)


def scan_write_tools(policy_py: Path) -> list[str]:
    src = policy_py.read_text(encoding="utf-8")
    module = ast.parse(src)
    for node in ast.walk(module):
        # Plain assignment: WRITE_TOOLS = ...
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "WRITE_TOOLS":
                    names = _extract_write_tools_value(node.value)
                    if names is not None:
                        return names
        # Annotated assignment: WRITE_TOOLS: set[str] = ...
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "WRITE_TOOLS":
                names = _extract_write_tools_value(node.value)
                if names is not None:
                    return names
    raise ValidationError("WRITE_TOOLS literal not found in policy.py")


def assign_server(tool_name: str, write_tools: set[str]) -> str:
    if tool_name in write_tools:
        return CONTROL_SERVER
    prefix = tool_name.split("_", 1)[0]
    if prefix not in PREFIX_TO_SERVER_RO:
        raise ValidationError(
            f"tool {tool_name!r} has unknown prefix {prefix!r}; update PREFIX_TO_SERVER_RO"
        )
    return PREFIX_TO_SERVER_RO[prefix]


def diff_sets(label_a: str, set_a: set, label_b: str, set_b: set) -> str:
    only_a = sorted(set_a - set_b)
    only_b = sorted(set_b - set_a)
    if not only_a and not only_b:
        return ""
    parts: list[str] = []
    if only_a:
        parts.append(f"  only in {label_a}: {only_a}")
    if only_b:
        parts.append(f"  only in {label_b}: {only_b}")
    return "\n".join(parts)


def assert_pinned_commit(repo: Path, commit: str | None) -> str:
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if commit and head != commit:
        # Not a hard failure: caller may run on a working tree. Print and
        # continue; T3/T7 will catch any drift.
        print(
            f"warning: source repo HEAD {head} != pinned commit {commit}",
            file=sys.stderr,
        )
    return head


def validate(source_repo: Path, expected_commit: str | None = None) -> int:
    if not source_repo.exists():
        raise ValidationError(f"source repo not found: {source_repo}")
    inventory_path = INVENTORY_PATH
    if not inventory_path.exists():
        raise ValidationError(f"inventory not found: {inventory_path}")

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    # T1: schema (light-weight check; required keys, types).
    required_keys = {"source_commit", "captured_utc", "totals", "servers", "tools"}
    missing = required_keys - inventory.keys()
    if missing:
        raise ValidationError(f"T1 schema: missing keys {sorted(missing)}")
    if not isinstance(inventory["tools"], list):
        raise ValidationError("T1 schema: tools must be a list")
    # Validate inventory["servers"] keys upfront so per-server lookups never
    # KeyError later (verify findings ADV-008 critical / ADV-004 medium).
    if not isinstance(inventory["servers"], dict):
        raise ValidationError("T1 schema: servers must be a dict")
    server_keys = set(inventory["servers"].keys())
    unknown = server_keys - ALL_SERVERS
    if unknown:
        raise ValidationError(
            f"T1 schema: inventory.servers contains unknown server key(s) {sorted(unknown)}; "
            f"expected exactly {sorted(ALL_SERVERS)}"
        )
    missing_servers = ALL_SERVERS - server_keys
    if missing_servers:
        raise ValidationError(
            f"T1 schema: inventory.servers missing required server key(s) {sorted(missing_servers)}"
        )
    for t in inventory["tools"]:
        if not {"name", "server", "mutating"} <= t.keys():
            raise ValidationError(f"T1 schema: tool entry missing required keys: {t}")
        if not isinstance(t["mutating"], bool):
            raise ValidationError(
                f"T1 schema: tool {t.get('name')!r} mutating must be bool"
            )
        if t["server"] not in ALL_SERVERS:
            raise ValidationError(
                f"T1 schema: tool {t['name']!r} server {t['server']!r} not in {ALL_SERVERS}"
            )

    # Pinned commit advisory.
    head = assert_pinned_commit(source_repo, expected_commit or inventory["source_commit"])

    # Re-scan source repo at HEAD.
    server_py = source_repo / "mcp" / "src" / "homelab_mcp" / "server.py"
    policy_py = source_repo / "mcp" / "src" / "homelab_mcp" / "policy.py"

    scanned_tools = scan_server_tools(server_py)
    scanned_writes = scan_write_tools(policy_py)

    inventory_tools = [t["name"] for t in inventory["tools"]]
    inventory_mutating = sorted(t["name"] for t in inventory["tools"] if t["mutating"])
    inventory_readonly = sorted(t["name"] for t in inventory["tools"] if not t["mutating"])

    # T2: counts.
    if len(inventory_tools) != EXPECTED_TOTALS["tools"]:
        raise ValidationError(
            f"T2 counts: tools = {len(inventory_tools)} != {EXPECTED_TOTALS['tools']}"
        )
    if len(inventory_mutating) != EXPECTED_TOTALS["writes"]:
        raise ValidationError(
            f"T2 counts: mutating = {len(inventory_mutating)} != {EXPECTED_TOTALS['writes']}"
        )
    if len(inventory_readonly) != EXPECTED_TOTALS["readonly"]:
        raise ValidationError(
            f"T2 counts: readonly = {len(inventory_readonly)} != {EXPECTED_TOTALS['readonly']}"
        )

    # T3: set-equality of tool names (the crucial RC check vs sum-only).
    diff = diff_sets(
        "scanned (source HEAD)", set(scanned_tools),
        "inventory", set(inventory_tools),
    )
    if diff:
        raise ValidationError("T3 set-equality: scanned vs inventory mismatch:\n" + diff)

    # T4: pairwise disjointness across servers.
    by_server: dict[str, set[str]] = {s: set() for s in ALL_SERVERS}
    for t in inventory["tools"]:
        by_server[t["server"]].add(t["name"])
    for srv in ALL_SERVERS:
        for other in ALL_SERVERS:
            if srv >= other:
                continue
            overlap = by_server[srv] & by_server[other]
            if overlap:
                raise ValidationError(
                    f"T4 disjoint: {srv} and {other} both contain {sorted(overlap)}"
                )

    # T5: write-isolation.
    for t in inventory["tools"]:
        if t["mutating"] and t["server"] != CONTROL_SERVER:
            raise ValidationError(
                f"T5 write-isolation: mutating tool {t['name']!r} on non-control server {t['server']!r}"
            )
        if not t["mutating"] and t["server"] == CONTROL_SERVER:
            raise ValidationError(
                f"T5 write-isolation: non-mutating tool {t['name']!r} on control server"
            )

    # T6: strict decorator scan already enforced in scan_server_tools().

    # T7: write-tool set match.
    diff = diff_sets(
        "policy.py:WRITE_TOOLS", set(scanned_writes),
        "inventory mutating=true", set(inventory_mutating),
    )
    if diff:
        raise ValidationError("T7 write-tools-match: WRITE_TOOLS vs inventory mismatch:\n" + diff)

    # Per-server count cross-check.
    for srv, props in inventory["servers"].items():
        actual = len(by_server[srv])
        if actual != props["tools"]:
            raise ValidationError(
                f"per-server count: {srv} has {actual} tools, header says {props['tools']}"
            )

    # Independent assignment cross-check (every tool should be re-assigned to
    # the same server by the deterministic rule).
    write_set = set(scanned_writes)
    for t in inventory["tools"]:
        expected = assign_server(t["name"], write_set)
        if t["server"] != expected:
            raise ValidationError(
                f"assignment: tool {t['name']!r} assigned to {t['server']!r}; "
                f"deterministic rule says {expected!r}"
            )

    print(f"OK: 133/29/104 verified at source HEAD {head[:12]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-repo",
        required=True,
        help="Path to the homelab source repo (required; no default to keep this cross-platform)",
    )
    parser.add_argument(
        "--commit",
        default=None,
        help="Expected source commit (advisory; warning only if mismatch)",
    )
    args = parser.parse_args()
    try:
        return validate(Path(args.source_repo), args.commit)
    except ValidationError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
