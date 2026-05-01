"""Phase 0.4: prevent re-introducing homelab-specific values into source.

This is a static guard that runs as part of the unit test suite. Source
files in `mcp/src/homelab_mcp/` MUST NOT contain operator hostnames, IPs,
usernames, or DNS zones — those values come from environment variables
configured by the K8s Deployment manifest.

If you fail this test, the fix is to:
  * read the value from a new or existing helper in ``settings.py``
  * add an env var to ``apps/platform/mcp-proxy/deployment.yaml``
  * NOT to add an exception here

Allowed exceptions are listed under ``ALLOWLIST`` and require a comment
explaining why the literal is public-safe.
"""
from __future__ import annotations

import re
from pathlib import Path

# Patterns that indicate operator-specific data. Each pattern's match groups
# point at the offending substring for the assertion message.
PRIVATE_PATTERNS = [
    # Hostnames matching *.hont.ro (homelab apex domain).
    (re.compile(r"\b[a-z0-9][a-z0-9.\-]*\.hont\.ro\b", re.IGNORECASE), "private hostname (*.hont.ro)"),
    # The bare apex domain itself when used as a literal value, not as part of
    # a longer string. Only flag if quoted or comparison-wrapped to avoid
    # matching this docstring.
    (re.compile(r"['\"]hont\.ro['\"]"), "apex domain literal 'hont.ro'"),
    # RFC1918 IPs in an assignment / argument context (not in a comment).
    (re.compile(
        r'(?<![#\w])'
        r'(?:'
        r'10\.(?:\d{1,3}\.){2}\d{1,3}'
        r'|172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3}'
        r'|192\.168\.\d{1,3}\.\d{1,3}'
        r')'
    ), "private IPv4 literal"),
    # Operator's username as a default for an env var.
    (re.compile(r'env\([^)]*"HOMELAB_SSH_USER"[^)]*"dragos"', re.IGNORECASE), "HOMELAB_SSH_USER default 'dragos'"),
]

# (filename, line_substring) entries that are intentionally allowed. Comment
# in source MUST justify why each entry is public-safe.
ALLOWLIST: list[tuple[str, str]] = [
    # Empty for now. Future entries: e.g. "settings.py" might legitimately
    # include "127.0.0.1" in a comment about a localhost example.
]

SOURCE_DIR = Path(__file__).resolve().parents[1] / "src" / "homelab_mcp"


def _is_allowlisted(file_name: str, line: str) -> bool:
    return any(file_name == fn and substr in line for fn, substr in ALLOWLIST)


def _is_in_comment(line: str, match_start: int) -> bool:
    """Return True if the matched span is inside a #-comment."""
    pre = line[:match_start]
    # Strip strings to avoid mistaking a # inside a literal as a comment marker.
    # Conservative heuristic: if there's a # in pre that's not inside the
    # most-recent open quote, treat the rest as a comment.
    in_single = False
    in_double = False
    i = 0
    while i < len(pre):
        c = pre[i]
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "#" and not in_single and not in_double:
            return True
        i += 1
    return False


def _is_in_docstring(content: str, abs_pos: int) -> bool:
    """Return True if abs_pos is inside a triple-quoted string.

    Counts ``\"\"\"`` and ``'''`` occurrences before abs_pos; odd count = inside.
    """
    triple_double = content.count('"""', 0, abs_pos)
    triple_single = content.count("'''", 0, abs_pos)
    return (triple_double % 2 == 1) or (triple_single % 2 == 1)


def _scan_file(path: Path) -> list[str]:
    findings: list[str] = []
    content = path.read_text(encoding="utf-8")
    file_name = path.name
    for pattern, description in PRIVATE_PATTERNS:
        for m in pattern.finditer(content):
            # Skip docstrings: documentation legitimately mentions example
            # values. Production code paths never end up inside a docstring.
            if _is_in_docstring(content, m.start()):
                continue
            line_start = content.rfind("\n", 0, m.start()) + 1
            line_end = content.find("\n", m.start())
            line = content[line_start:line_end if line_end != -1 else len(content)]
            line_no = content.count("\n", 0, m.start()) + 1
            # Comments may legitimately use private IPs as illustrative
            # examples (e.g., "# IP or hostname of the UDM").
            if _is_in_comment(line, m.start() - line_start):
                continue
            if _is_allowlisted(file_name, line):
                continue
            findings.append(
                f"{file_name}:{line_no}: {description}: {m.group(0)} | {line.strip()}"
            )
    return findings


def test_no_homelab_specifics_in_source():
    findings: list[str] = []
    for path in sorted(SOURCE_DIR.rglob("*.py")):
        # Skip the test files themselves (this file lives elsewhere; defensive).
        if "tests" in path.parts:
            continue
        findings.extend(_scan_file(path))

    if findings:
        joined = "\n  ".join(findings)
        raise AssertionError(
            f"Found {len(findings)} homelab-specific literal(s) in source. "
            f"Move each to an env var read via settings.py and update "
            f"apps/platform/mcp-proxy/deployment.yaml. Findings:\n  {joined}"
        )
