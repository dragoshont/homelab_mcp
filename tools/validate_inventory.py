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

# AS-14: recognised decorator forms. Anything else fails strict mode.
RECOGNISED_DECORATOR_ATTRS = {"tool"}


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
        recognised = False
        for dec in node.decorator_list:
            attr = None
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                attr = dec.func.attr
            elif isinstance(dec, ast.Attribute):
                attr = dec.attr
            elif isinstance(dec, ast.Name):
                attr = dec.id
            if attr in RECOGNISED_DECORATOR_ATTRS:
                tools.append(node.name)
                recognised = True
                break
        # AS-14: any decorated top-level function with NO recognised decorator
        # is a strict-mode failure. Decorated functions intended to not be
        # tools must live elsewhere.
        if not recognised:
            raise ValidationError(
                f"strict-mode: function {node.name!r} has decorators but none match "
                f"recognised tool forms {RECOGNISED_DECORATOR_ATTRS}"
            )
    return tools


def scan_write_tools(policy_py: Path) -> list[str]:
    src = policy_py.read_text(encoding="utf-8")
    module = ast.parse(src)
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "WRITE_TOOLS":
                value = node.value
                if isinstance(value, ast.Call) and value.args:
                    seq = value.args[0]
                    if isinstance(seq, (ast.Set, ast.List, ast.Tuple)):
                        return sorted(
                            e.value for e in seq.elts if isinstance(e, ast.Constant)
                        )
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
    for srv, props in inventory["totals"].__class__ is dict and inventory.get("servers", {}).items() or []:
        pass  # placeholder; per-server totals validated below.

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
        default=str(Path(r"C:\src\homelab")),
        help="Path to the homelab source repo",
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
