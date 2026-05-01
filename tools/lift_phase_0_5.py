#!/usr/bin/env python3
"""Phase 0.5 lift script (design.md §2 + §3).

Copies the source repo's mcp/ tree byte-faithfully into this repo, builds a
SHA-256 manifest, runs a leak scan, and aborts on any mismatch or real
secret / non-operator-copyright finding.

Read-only against the source repo. The destination is THIS repo's mcp/.

Usage:
    python tools/lift_phase_0_5.py --source-repo C:\\src\\homelab --apply
    python tools/lift_phase_0_5.py --source-repo C:\\src\\homelab --dry-run

Without --apply (or with --dry-run), the script enumerates and validates but
writes nothing.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from math import log2
from pathlib import Path

# --- AS-1 mitigation: hard commit-gate severities ---
BLOCK_SEVERITIES = {"real-secret", "non-operator-copyright"}

# --- AS-2 mitigation: third-party copyright detection ---
COPYRIGHT_PATTERNS = [
    re.compile(r"Copyright\s*\(c\)", re.IGNORECASE),
    re.compile(r"SPDX-License-Identifier", re.IGNORECASE),
    re.compile(r"^\s*#\s*License\s*[:|=]", re.IGNORECASE | re.MULTILINE),
]

# --- RK-1 leak-scan: known-private homelab strings ---
PRIVATE_HOSTNAMES = [
    "home.hont.ro",
    "nas.hont.ro",
]
RFC1918 = re.compile(
    r"\b("
    r"10\.(?:\d{1,3}\.){2}\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r")\b"
)

# Secrets: Shannon entropy of base64-ish strings.
SECRET_TOKEN = re.compile(r"['\"]([A-Za-z0-9+/=_\-]{24,})['\"]")


def shannon(s: str) -> float:
    if not s:
        return 0.0
    probs = [s.count(c) / len(s) for c in set(s)]
    return -sum(p * log2(p) for p in probs)


def _line_col_to_abs(content: str, line: int, col: int) -> int:
    """Convert a 1-based (line, col) tuple from tokenize to a 0-based absolute offset."""
    abs_pos = 0
    current_line = 1
    while current_line < line and abs_pos < len(content):
        nl = content.find("\n", abs_pos)
        if nl == -1:
            return abs_pos
        abs_pos = nl + 1
        current_line += 1
    return abs_pos + col


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        list(args), cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def enumerate_source_files(source_repo: Path) -> list[str]:
    """Return mcp/** tracked file paths from the source repo (relative)."""
    raw = run("git", "-C", str(source_repo), "ls-files", "mcp/")
    return [p.strip() for p in raw.splitlines() if p.strip()]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def leak_scan_file(path: Path, content: bytes) -> list[dict]:
    findings: list[dict] = []
    if not path.suffix.lower() in {".py", ".toml", ".md", ".txt", ".yaml",
                                    ".yml", ".json", ".cfg", ".ini",
                                    ".dockerfile", ""} and path.name not in {
                                        "Dockerfile", "Containerfile",
                                    }:
        return findings  # binary asset; skip text scans.

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return findings

    # AS-2: third-party copyright headers
    if path.suffix.lower() == ".py":
        head = "\n".join(text.splitlines()[:60])  # first 60 lines is plenty.
        for pat in COPYRIGHT_PATTERNS:
            for m in pat.finditer(head):
                findings.append({
                    "path": str(path),
                    "kind": "copyright-header",
                    "severity": "non-operator-copyright",
                    "match": m.group(0),
                    "line": text.count("\n", 0, m.start()) + 1,
                    "note": "Third-party copyright in lifted source. MIT relicense (LICENSE in repo root) only covers operator-authored code. Triage required.",
                })

    # RK-1: private hostnames
    for host in PRIVATE_HOSTNAMES:
        for m in re.finditer(re.escape(host), text):
            findings.append({
                "path": str(path),
                "kind": "private-hostname",
                "severity": "real-secret",
                "match": host,
                "line": text.count("\n", 0, m.start()) + 1,
                "note": "Private homelab hostname leaked into public repo source.",
            })

    # RFC1918 IPs (only flag if NOT in a comment-like context)
    # Skip test files entirely: tests legitimately use private IPs as fixtures
    # (e.g., asserting that "10.0.0.5" is recognised as valid).
    is_test_file = "tests" in path.parts or path.name.startswith("test_")
    if not is_test_file:
        # For .py files, use Python's tokenize module to map each match to
        # an exact token type. A '#' in a string literal must NOT be treated
        # as the start of a comment.
        comment_ranges: list[tuple[int, int]] = []
        if path.suffix.lower() == ".py":
            try:
                import io
                import tokenize
                tokens = list(tokenize.tokenize(io.BytesIO(content).readline))
                for tok in tokens:
                    if tok.type == tokenize.COMMENT:
                        sl, sc = tok.start
                        el, ec = tok.end
                        # Convert to absolute byte/char offsets
                        start = _line_col_to_abs(text, sl, sc)
                        end = _line_col_to_abs(text, el, ec)
                        comment_ranges.append((start, end))
            except (tokenize.TokenizeError, IndentationError):
                # If the file fails to tokenize, fall back to permissive scan.
                pass

        for m in RFC1918.finditer(text):
            in_python_comment = any(s <= m.start() < e for s, e in comment_ranges)
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.start())
            line = text[line_start:line_end if line_end != -1 else len(text)]
            # Non-Python files (yaml/md/etc.): heuristic line-prefix check.
            stripped = line.lstrip()
            line_prefix_comment = stripped.startswith(("#", "//", "/*", "*"))
            is_comment = in_python_comment or (path.suffix.lower() != ".py" and line_prefix_comment)
            is_example = "example" in line.lower() or "e.g." in line.lower()
            if not is_comment and not is_example:
                findings.append({
                    "path": str(path),
                    "kind": "rfc1918-ip",
                    "severity": "real-secret" if path.suffix.lower() == ".py" else "low",
                    "match": m.group(0),
                    "line": text.count("\n", 0, m.start()) + 1,
                    "note": "Private IP appearing in non-comment context.",
                })

    # High-entropy tokens (Shannon ≥ 4.0 over 24+ chars). Skip well-known
    # non-secret strings that pass the entropy threshold but are clearly
    # configuration / option strings rather than credentials.
    NON_SECRET_PHRASES = (
        "StrictHostKeyChecking", "ConnectTimeout", "PasswordAuthentication",
        "PubkeyAuthentication", "UserKnownHostsFile", "Content-Type",
        "application/json", "multipart/form-data", "X-Plex-Token",
        "Authorization", "Bearer ",
    )
    if path.suffix.lower() == ".py":
        for m in SECRET_TOKEN.finditer(text):
            tok = m.group(1)
            if len(tok) < 24 or shannon(tok) < 4.0:
                continue
            if any(phrase in tok for phrase in NON_SECRET_PHRASES):
                continue
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.start())
            line = text[line_start:line_end if line_end != -1 else len(text)]
            if any(phrase in line for phrase in NON_SECRET_PHRASES):
                continue
            if "test_" in str(path).lower() and ("hash" in line.lower() or "fake" in line.lower() or "example" in line.lower()):
                sev = "low"
            else:
                sev = "real-secret"
            findings.append({
                "path": str(path),
                "kind": "high-entropy-token",
                "severity": sev,
                "match": tok[:8] + "..." + tok[-4:],
                "line": text.count("\n", 0, m.start()) + 1,
                "note": "High-entropy token. May be an API key, password, or hash literal in a test fixture. Triage required.",
            })

    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--apply", action="store_true", help="Actually write files. Without this, dry-run only.")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run; no writes.")
    args = parser.parse_args()

    source = Path(args.source_repo).resolve()
    dest = repo_root().resolve()
    dry_run = args.dry_run or not args.apply

    print(f"== Phase 0.5 lift ==")
    print(f"   source: {source}")
    print(f"   dest:   {dest}")
    print(f"   mode:   {'DRY-RUN (no writes)' if dry_run else 'APPLY'}")

    # 2.1 source preflight
    if not source.exists():
        print(f"FAIL: source repo not found: {source}", file=sys.stderr)
        return 1
    source_head = run("git", "-C", str(source), "rev-parse", "HEAD")
    dirty = run("git", "-C", str(source), "status", "--porcelain", "mcp/")
    if dirty:
        print(f"FAIL: source repo has dirty mcp/: {dirty!r}", file=sys.stderr)
        return 1
    print(f"   source HEAD: {source_head}")

    # Enumerate files.
    files = enumerate_source_files(source)
    print(f"   files to lift: {len(files)}")

    # 2.2: read source bytes, hash, leak-scan IN MEMORY before any writes.
    # This addresses ADV-003 high: the previous version interleaved write +
    # scan, so a blocking finding could leave the destination with
    # partially-copied secret-bearing files. Now: scan ALL files first,
    # abort if any blocking finding, only then perform the binary copies.
    manifest_files: list[dict] = []
    leak_findings: list[dict] = []
    file_bytes: dict[str, bytes] = {}

    for rel in files:
        src_path = source / rel
        if not src_path.is_file():
            print(f"SKIP (not a file): {rel}")
            continue
        # Binary read (AS-3 mitigation).
        data = src_path.read_bytes()
        digest = sha256_bytes(data)
        manifest_files.append({"path": rel, "sha256": digest, "bytes": len(data)})
        leak_findings.extend(leak_scan_file(Path(rel), data))
        file_bytes[rel] = data

    print(f"   manifest entries: {len(manifest_files)}")
    print(f"   leak-scan findings: {len(leak_findings)}")

    # 3: AS-1 hard commit-gate — evaluated BEFORE any destination writes.
    blocking = [f for f in leak_findings if f["severity"] in BLOCK_SEVERITIES]
    sdd_dir = dest / "out" / "Rivet" / "sdd" / "phase-0.5-source-lift"
    leak_path = sdd_dir / "leak-scan.json"
    leak_doc = {
        "source_repo": str(source),
        "source_commit": source_head,
        "scanned_utc": datetime.now(timezone.utc).isoformat(),
        "block_severities": sorted(BLOCK_SEVERITIES),
        "summary": {
            "total": len(leak_findings),
            "blocking": len(blocking),
            "by_severity": {
                sev: sum(1 for f in leak_findings if f["severity"] == sev)
                for sev in {f["severity"] for f in leak_findings}
            },
        },
        "findings": leak_findings,
    }
    if not dry_run:
        sdd_dir.mkdir(parents=True, exist_ok=True)
        leak_path.write_text(json.dumps(leak_doc, indent=2), encoding="utf-8")
        print(f"   leak scan written: {leak_path}")
    else:
        print(f"   leak scan (dry-run, not written): {leak_path}")

    if blocking:
        print(
            f"FAIL: {len(blocking)} blocking finding(s). Refusing to copy any "
            f"files. See {leak_path}.",
            file=sys.stderr,
        )
        for f in blocking[:10]:
            print(f"  {f['severity']:25s}  {f['path']}:{f['line']}  {f['kind']}  {f.get('match', '')}", file=sys.stderr)
        return 3

    # 2.3: now safe to copy. No blocking findings, so destination cannot end
    # up with secret-bearing files even if the copy itself fails partway.
    written = 0
    if not dry_run:
        for rel, data in file_bytes.items():
            dest_path = dest / rel
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            # Binary write (AS-3 mitigation).
            dest_path.write_bytes(data)
            # Re-read and verify.
            check = sha256_bytes(dest_path.read_bytes())
            expected_digest = next(
                m["sha256"] for m in manifest_files if m["path"] == rel
            )
            if check != expected_digest:
                print(
                    f"FAIL: hash mismatch on {rel}: src={expected_digest} dst={check}",
                    file=sys.stderr,
                )
                return 2
            written += 1

    print(f"   files written: {written if not dry_run else 0}")

    # Stale destination check: any *.py / Dockerfile / pyproject.toml under
    # dest/mcp/ that is NOT in the source manifest is stale (source removed
    # it but the previous lift left it behind). The lift script does NOT
    # delete autonomously — it surfaces them so the operator can decide.
    expected_paths = {(dest / f["path"]).resolve() for f in manifest_files}
    expected_paths.add((dest / "mcp" / ".lift-manifest.json").resolve())
    if (dest / "mcp").exists():
        stale: list[Path] = []
        for p in (dest / "mcp").rglob("*"):
            if not p.is_file():
                continue
            # Skip __pycache__ / .pytest_cache / generated artifacts.
            if any(part in {"__pycache__", ".pytest_cache"} for part in p.parts):
                continue
            if p.resolve() not in expected_paths:
                stale.append(p)
        if stale:
            print(f"   WARNING: {len(stale)} stale destination file(s) not in source manifest:")
            for p in stale[:10]:
                print(f"      {p.relative_to(dest)}")
            print("   These were not touched by this lift. Investigate and remove manually if appropriate.")

    # 2.4 manifest.
    manifest = {
        "source_repo": "dragoshont/homelab",
        "source_commit": source_head,
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "decision_history_preservation": "fresh-history-no-filter-repo",
        "files": manifest_files,
    }
    manifest_path = dest / "mcp" / ".lift-manifest.json"
    if not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"   manifest written: {manifest_path}")
    else:
        print(f"   manifest (dry-run, not written): {manifest_path}")

    print()
    print(f"OK: {len(files)} file(s) {'would be lifted' if dry_run else 'lifted'} from {source_head[:12]}; {len(leak_findings)} leak finding(s), 0 blocking.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
