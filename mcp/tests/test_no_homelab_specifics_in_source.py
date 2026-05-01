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
    """Return True if abs_pos falls inside a PEP 257 docstring.

    BUG-012 fix: previous implementation treated *any* triple-quoted
    string as a docstring, so a homelab-specific literal smuggled into
    a triple-quoted string assigned to a module/class variable would
    silently bypass this guard. PEP 257 only counts a string literal
    that is the **first statement** of a module, class, or function
    body as a docstring.

    Uses ``ast`` to enumerate true docstring offsets and falls back to
    refusing the skip on parse failure (safer default — a malformed
    file can't smuggle a literal past the guard by failing to parse).
    """
    import ast
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False  # Don't grant the skip on unparseable source.

    lines: list[str] = content.split("\n")
    line_starts: list[int] = [0]
    cumulative = 0
    for line in lines:
        cumulative += len(line) + 1  # +1 for the \n separator
        line_starts.append(cumulative)

    def _docstring_node(body: list[ast.stmt]) -> ast.Constant | None:
        if not body:
            return None
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            return first.value
        return None

    docstring_ranges: list[tuple[int, int]] = []
    nodes_with_body: list[ast.AST] = [tree]
    nodes_with_body.extend(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    for node in nodes_with_body:
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        ds = _docstring_node(body)
        if ds is None:
            continue
        # ast Constant nodes have lineno/col_offset (start) and
        # end_lineno/end_col_offset (end). Convert to absolute offsets.
        start_line = ds.lineno
        start_col = ds.col_offset
        end_line = getattr(ds, "end_lineno", start_line)
        end_col = getattr(ds, "end_col_offset", start_col)
        if start_line is None or end_line is None:
            continue
        start_abs = line_starts[start_line - 1] + start_col
        end_abs = line_starts[end_line - 1] + end_col
        docstring_ranges.append((start_abs, end_abs))

    return any(start <= abs_pos < end for start, end in docstring_ranges)


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

# --- BUG-012 regression tests for _is_in_docstring ---


def test_is_in_docstring_recognises_module_docstring():
    """BUG-012: an offset inside the module docstring must be classified as docstring."""
    src = '"""module doc here\nspans lines\n"""\n\nx = 1\n'
    pos = src.find("module doc")
    assert _is_in_docstring(src, pos) is True


def test_is_in_docstring_recognises_function_docstring():
    """BUG-012: a function's first-statement string is a docstring."""
    src = 'def f():\n    """func doc"""\n    return 1\n'
    pos = src.find("func doc")
    assert _is_in_docstring(src, pos) is True


def test_is_in_docstring_rejects_assigned_triple_quoted_string():
    """BUG-012 core: a triple-quoted string assigned to a variable is NOT a docstring.

    A homelab-specific value smuggled into MY_TEMPLATE = '''...''' must NOT
    be silently skipped by the no-homelab-specifics guard.
    """
    src = 'MY_TEMPLATE = """home.hont.ro is special"""\n'
    pos = src.find("home.hont.ro")
    assert _is_in_docstring(src, pos) is False


def test_is_in_docstring_rejects_inline_triple_quoted_expression():
    """BUG-012: a triple-quoted string used as an inline expression is NOT a docstring."""
    src = 'def f():\n    return """home.hont.ro"""\n'
    pos = src.find("home.hont.ro")
    assert _is_in_docstring(src, pos) is False
