#!/usr/bin/env python3
"""Phase 0.5 G-5 — lift-completeness check.

Where ``validate_inventory.py`` checks that the SOURCE repo's server.py
still matches the inventory, this script checks the LIFTED file in this
repo's ``mcp/src/homelab_mcp/server.py`` matches the inventory.

Without this check, a partial lift (some files copied, some missed) could
pass G-2 (source unchanged) yet the new repo would be broken.

Exits 0 if set(scanned_lifted_tools) == set(inventory_tools).
Exits non-zero with a diff otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Reuse the AST scanner from validate_inventory so the rules are identical.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_inventory import (  # noqa: E402  pylint:disable=wrong-import-position
    ValidationError,
    scan_server_tools,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LIFTED_SERVER_PY = REPO_ROOT / "mcp" / "src" / "homelab_mcp" / "server.py"
INVENTORY_JSON = REPO_ROOT / "docs" / "migration" / "tool-inventory.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lifted-server",
        type=Path,
        default=LIFTED_SERVER_PY,
        help="Path to the lifted server.py (default: mcp/src/homelab_mcp/server.py)",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=INVENTORY_JSON,
        help="Path to docs/migration/tool-inventory.json",
    )
    args = parser.parse_args()

    if not args.lifted_server.exists():
        print(f"FAIL: lifted server.py not found: {args.lifted_server}", file=sys.stderr)
        return 1
    if not args.inventory.exists():
        print(f"FAIL: inventory not found: {args.inventory}", file=sys.stderr)
        return 1

    try:
        scanned_list = scan_server_tools(args.lifted_server)
    except ValidationError as e:
        print(f"FAIL: cannot AST-scan lifted server.py: {e}", file=sys.stderr)
        return 2

    # Detect duplicates BEFORE collapsing into a set: two @mcp.tool decorators
    # on the same function name produce a list with duplicates that the set
    # comparison silently collapses, hiding a real bug in source.
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in scanned_list:
        if name in seen:
            duplicates.append(name)
        seen.add(name)
    if duplicates:
        print(
            f"FAIL: lifted server.py has duplicate @mcp.tool decorators on: "
            f"{sorted(set(duplicates))}",
            file=sys.stderr,
        )
        return 4

    scanned = set(scanned_list)
    try:
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
        inventory_names = [t["name"] for t in inventory["tools"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        # Malformed/missing inventory must produce a single FAIL line and a
        # non-zero exit code rather than a raw traceback, so the gate stays
        # deterministic in CI logs.
        print(
            f"FAIL: could not load inventory at {args.inventory}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 6

    # Detect duplicates in the INVENTORY too: a malformed inventory could
    # collide names that the set-comparison silently de-duplicates.
    seen_inv: set[str] = set()
    inv_dups: list[str] = []
    for name in inventory_names:
        if name in seen_inv:
            inv_dups.append(name)
        seen_inv.add(name)
    if inv_dups:
        print(
            f"FAIL: inventory contains duplicate tool name(s): "
            f"{sorted(set(inv_dups))}",
            file=sys.stderr,
        )
        return 5

    expected = set(inventory_names)

    missing = sorted(expected - scanned)
    extra = sorted(scanned - expected)

    if missing or extra:
        print("FAIL: lift incomplete — set mismatch between lifted server.py and inventory.", file=sys.stderr)
        if missing:
            print(f"  in inventory but NOT in lifted server.py: {missing}", file=sys.stderr)
        if extra:
            print(f"  in lifted server.py but NOT in inventory: {extra}", file=sys.stderr)
        return 3

    print(f"OK: G-5 lift-completeness verified — {len(scanned)} tools, set equality with inventory, no duplicate decorators.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
