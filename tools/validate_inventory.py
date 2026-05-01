#!/usr/bin/env python3
"""Phase 0 inventory validator (design.md ss9.1, 12).

Validates docs/migration/tool-inventory.json against the source repo's
working-tree HEAD. The pinned source_commit in the inventory is advisory:
if HEAD differs the validator prints a warning and continues (drift
is caught by T3 set-equality, not by commit equality).

Usage:
    python tools/validate_inventory.py --source-repo PATH [--commit SHA]

--source-repo is REQUIRED (no platform-specific default).

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


def _read_source_file(path: Path, label: str) -> str:
    """Read a source file from the homelab repo, raising ValidationError on
    any IO/parse problem (Copilot review #2). The validator promises a
    `FAIL: ...` line, never a raw traceback.
    """
    if not path.exists():
        raise ValidationError(f"{label} not found: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValidationError(f"failed to read {label} {path}: {e}") from e


def scan_server_tools(server_py: Path) -> list[str]:
    """AST-scan server.py for @mcp.tool decorators, strict mode."""
    src = _read_source_file(server_py, "server.py")
    try:
        module = ast.parse(src)
    except SyntaxError as e:
        raise ValidationError(f"server.py {server_py} failed to parse: {e}") from e
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
                # Verify finding ADV-001 critical: @mcp.tool(name="override")
                # changes the registered tool name from the function name. The
                # validator currently uses node.name. Until override-honoring
                # is implemented, reject any kwargs/positional args on the
                # decorator so the inventory cannot diverge silently from the
                # registered name.
                if isinstance(dec, ast.Call) and (dec.args or dec.keywords):
                    raise ValidationError(
                        f"strict-mode: function {node.name!r} uses @mcp.tool(...) with "
                        f"arguments; this validator only supports bare @mcp.tool() "
                        f"and @mcp.tool. If overrides are intended, extend the validator "
                        f"to honor the registered name (e.g. name= kwarg)."
                    )
                tools.append(node.name)
                break
    # Verify run #4 ADV-001/002: if source has two top-level functions with
    # the same name and both are decorated, Python only registers the last
    # (last-write-wins on the module dict) but our list would contain both.
    # Reject that ambiguity rather than silently inflating the count.
    if len(set(tools)) != len(tools):
        dupes = sorted({n for n in tools if tools.count(n) > 1})
        raise ValidationError(
            f"strict-mode: server.py has duplicate top-level @mcp.tool function names {dupes}; "
            f"Python registers only the last definition, the validator refuses to guess"
        )
    return tools


def _extract_set_constants(seq: ast.expr) -> list[str] | None:
    """Return string constants from a Set/List/Tuple, or None if not such.

    If any element is NOT an `ast.Constant` string (e.g. an imported name,
    a function call, an unpacked starred expr) the set is not fully
    resolvable at parse time. Return None rather than a partial result
    (verify run #2 finding ADV-004 high) so the caller can report it as
    an unsupported form rather than silently treating a partial set as
    authoritative.
    """
    if not isinstance(seq, (ast.Set, ast.List, ast.Tuple)):
        return None
    names: list[str] = []
    for e in seq.elts:
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            names.append(e.value)
        else:
            # Anything not a string literal taints the set; refuse to guess.
            return None
    return sorted(names)


def _extract_write_tools_value(value: ast.expr | None) -> list[str] | None:
    """Pull tool names from any of the supported WRITE_TOOLS RHS forms.

    Supported (verify findings ADV-002 / ADV-008 high):
      - frozenset({"a", "b"}) / set({"a", "b"}) / tuple(["a", "b"]) / list(["a", "b"])
      - {"a", "b"} (bare set literal)
      - ["a", "b"] (bare list literal)
      - ("a", "b") (bare tuple literal)
    Both ast.Assign and ast.AnnAssign reach here.

    Copilot review #5 finding: explicitly restrict accepted call forms to
    `frozenset` / `set` / `tuple` / `list`. Previously ANY call wrapping a
    set literal was accepted (e.g. `foo({...})` would have been treated as
    a valid WRITE_TOOLS form), letting an unsupported runtime expression
    pass through.
    """
    if value is None:
        return None
    # Recognised builtin constructors that take a single iterable arg.
    _ALLOWED_CALL_NAMES = {"frozenset", "set", "tuple", "list"}
    if isinstance(value, ast.Call):
        # Only accept Name calls (e.g. frozenset(...)), not Attribute calls
        # (e.g. typing.FrozenSet(...) or some_module.frozenset(...)).
        func = value.func
        if isinstance(func, ast.Name) and func.id in _ALLOWED_CALL_NAMES and value.args:
            names = _extract_set_constants(value.args[0])
            if names is not None:
                return names
        # Other call forms are NOT accepted; fall through to None.
        return None
    # Bare set/list/tuple literal form.
    return _extract_set_constants(value)


def scan_write_tools(policy_py: Path) -> list[str]:
    src = _read_source_file(policy_py, "policy.py")
    try:
        module = ast.parse(src)
    except SyntaxError as e:
        raise ValidationError(f"policy.py {policy_py} failed to parse: {e}") from e
    # Only consider module-level assignments. ast.walk() would dive into
    # function/class bodies and let a nested-scope `WRITE_TOOLS = ...`
    # silently shadow an unrecognised module-level form (verify run #2
    # finding ADV-002).
    # Verify run #3 findings ADV-002/003 high: collect ALL module-level
    # WRITE_TOOLS assignments and reject duplicates rather than returning
    # the first match (which would be silently overridden at import time
    # by Python's last-write-wins semantics).
    matches: list[list[str]] = []
    for node in module.body:
        # Plain assignment: WRITE_TOOLS = ...
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "WRITE_TOOLS":
                    names = _extract_write_tools_value(node.value)
                    if names is not None:
                        matches.append(names)
        # Annotated assignment: WRITE_TOOLS: set[str] = ...
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "WRITE_TOOLS":
                names = _extract_write_tools_value(node.value)
                if names is not None:
                    matches.append(names)
    if not matches:
        raise ValidationError("WRITE_TOOLS literal not found in policy.py")
    if len(matches) > 1:
        raise ValidationError(
            f"strict-mode: policy.py has {len(matches)} module-level WRITE_TOOLS "
            f"definitions; the validator refuses to guess which is canonical"
        )
    return matches[0]


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
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        # Verify run #2 finding ADV-004 medium: a non-git --source-repo
        # crashed with a raw traceback. Convert to a controlled failure.
        raise ValidationError(
            f"source repo {repo} is not a git repository (or git is unavailable): "
            f"git rev-parse HEAD exited {e.returncode}"
        ) from e
    except FileNotFoundError as e:
        raise ValidationError(
            f"git executable not found while inspecting source repo {repo}"
        ) from e
    head = result.stdout.strip()
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

    # Copilot review #1: convert IO/encoding/JSON failures into
    # ValidationError so the user always sees a `FAIL: ...` line instead
    # of a raw traceback.
    try:
        inventory_text = inventory_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValidationError(f"failed to read inventory {inventory_path}: {e}") from e
    try:
        inventory = json.loads(inventory_text)
    except json.JSONDecodeError as e:
        raise ValidationError(f"failed to parse inventory JSON {inventory_path}: {e}") from e
    if not isinstance(inventory, dict):
        raise ValidationError(
            f"T1 schema: inventory must be a JSON object, got {type(inventory).__name__}"
        )

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
    # Each server entry must be a dict with an int 'tools' count
    # (verify run #2 finding ADV-004 high). An empty dict previously
    # KeyError'd later in per-server cross-check.
    for srv_name, props in inventory["servers"].items():
        if not isinstance(props, dict):
            raise ValidationError(
                f"T1 schema: inventory.servers[{srv_name!r}] must be an object, got {type(props).__name__}"
            )
        if "tools" not in props or not isinstance(props["tools"], int):
            raise ValidationError(
                f"T1 schema: inventory.servers[{srv_name!r}] missing required int 'tools' field"
            )
    # Detect duplicate tool names BEFORE per-entry validation so the error
    # message is unambiguous (verify run #2 finding ADV-001 medium).
    seen_names: set[str] = set()
    for t in inventory["tools"]:
        name = t.get("name") if isinstance(t, dict) else None
        if isinstance(name, str):
            if name in seen_names:
                raise ValidationError(
                    f"T1 schema: duplicate tool name {name!r} in inventory.tools"
                )
            seen_names.add(name)
    for t in inventory["tools"]:
        # Copilot review #2: catch non-dict entries here so we raise a
        # controlled T1 error instead of an AttributeError on `.keys()`.
        if not isinstance(t, dict):
            raise ValidationError(
                f"T1 schema: tool entry must be an object, got {type(t).__name__}"
            )
        if not {"name", "server", "mutating"} <= t.keys():
            raise ValidationError(f"T1 schema: tool entry missing required keys: {t}")
        # Verify run #2 finding ADV-004 high: a non-string name later
        # crashed `tool_name.split(...)` with AttributeError.
        if not isinstance(t["name"], str) or not t["name"]:
            raise ValidationError(
                f"T1 schema: tool entry has non-string or empty 'name': {t!r}"
            )
        if not isinstance(t["mutating"], bool):
            raise ValidationError(
                f"T1 schema: tool {t.get('name')!r} mutating must be bool"
            )
        if t["server"] not in ALL_SERVERS:
            raise ValidationError(
                f"T1 schema: tool {t['name']!r} server {t['server']!r} not in {ALL_SERVERS}"
            )
    # Verify run #2 finding ADV-007 high: validate the totals header
    # against the actual tools array, so a corrupted totals block can't
    # silently coexist with a valid tools list.
    totals = inventory.get("totals")
    if not isinstance(totals, dict):
        raise ValidationError("T1 schema: totals must be a dict")
    actual_tools = len(inventory["tools"])
    actual_writes = sum(1 for t in inventory["tools"] if t["mutating"])
    actual_readonly = actual_tools - actual_writes
    for label, actual in (
        ("tools", actual_tools),
        ("writes", actual_writes),
        ("readonly", actual_readonly),
    ):
        declared = totals.get(label)
        if declared != actual:
            raise ValidationError(
                f"T1 schema: totals.{label} = {declared!r} but tools array has {actual}"
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
