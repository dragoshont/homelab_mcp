# Design — phase-0.5-source-lift

**Workflow:** `phase-0.5-source-lift` (#0)
**Reads with:** `spec.md`, `contract.md`

---

## 1. Architecture

The lift is a one-way file-tree copy. No new modules, no new abstractions.

```
   dragoshont/homelab (private, source of truth today)
   └─ mcp/
       ├─ src/homelab_mcp/{*.py}        ─┐
       ├─ tests/{*.py, conftest.py}     ─┤  copied byte-identical
       ├─ Dockerfile                     ─┤  via SHA-256 manifest
       ├─ pyproject.toml                 ─┤
       └─ conftest.py (if present)      ─┘
                                              │
                                              ▼
   dragoshont/homelab_mcp (public, source of truth tomorrow)
   └─ mcp/                                + .lift-manifest.json
```

`.lift-manifest.json` records the source-repo commit and a SHA-256 for each
lifted file. Subsequent SDDs (Phase 1+) read this manifest to verify the
lifted tree has not been mutated and to detect drift in the source repo
before PR #6 deletes `mcp/` from it.

## 2. Procedure

Executed by Step 5 of this SDD, only after the implement-gate is approved.

### 2.1 Source preflight (read-only)

1. `cd C:\src\homelab && git rev-parse HEAD` → `SOURCE_HEAD`
2. `cd C:\src\homelab && git status --porcelain mcp/` → must be empty (no
   uncommitted changes in source `mcp/`); if not, abort and surface the dirty
   state.
3. Enumerate `mcp/**` excluding the standard ignores from
   `C:\src\homelab\.gitignore`. List of files to lift is the output.

### 2.2 Compute manifest in memory

```text
for each path P in the enumeration:
  bytes = read(C:\src\homelab\P)
  hash  = sha256(bytes)
  manifest.append({path: P, sha256: hash, bytes: len(bytes)})
manifest.source_commit = SOURCE_HEAD
manifest.captured_utc  = now()
```

### 2.3 Copy

For each entry, perform a **binary-faithful** copy:
```text
bytes = [System.IO.File]::ReadAllBytes(source_path)
[System.IO.File]::WriteAllBytes(dest_path, bytes)
```
Do NOT use `Copy-Item` for `*.py`/`Dockerfile`/`pyproject.toml` because
Windows + git `core.autocrlf` can rewrite line endings on text files,
producing a hash mismatch (AS-3 mitigation).

After every copy, recompute SHA-256 from the destination raw bytes and
assert it matches the source manifest entry. If any hash mismatches,
**abort** and clean up partially-copied files (leave `mcp/` empty in the
new repo).

### 2.4 Manifest commit

Write `C:\src\homelab_mcp\mcp\.lift-manifest.json`:

```json
{
  "source_repo": "dragoshont/homelab",
  "source_commit": "<SOURCE_HEAD>",
  "captured_utc": "2026-05-01T...Z",
  "decision_history_preservation": "fresh-history-no-filter-repo",
  "files": [
    {"path": "mcp/src/homelab_mcp/server.py", "sha256": "...", "bytes": 12345},
    ...
  ]
}
```

### 2.5 Verification

1. `python tools/validate_inventory.py --source-repo C:\src\homelab` → exit 0
   (proves the SOURCE repo's monolith still has the pinned 133/29 set —
   does NOT prove the lift completeness on its own; AS-4 mitigation below).
2. **G-5 (lift-completeness, AS-4 mitigation):** Re-run the same
   AST-based scan on `C:\src\homelab_mcp\mcp\src\homelab_mcp\server.py`
   (the lifted file) and assert the resulting tool name set EQUALS the
   set in `docs/migration/tool-inventory.json`. The Step 5 implementation
   adds a small wrapper script `tools/verify_lift.py` that does this.
   Without this check, G-2 only validates the source repo, not the lift.
3. `$env:PYTHONPATH = (Resolve-Path mcp\src) ; python -m pytest mcp/tests -q` → `102 passed`
4. `python -c "import json,hashlib,pathlib; m=json.loads(open('mcp/.lift-manifest.json').read()); fail=[f for f in m['files'] if hashlib.sha256(pathlib.Path(f['path']).read_bytes()).hexdigest() != f['sha256']]; assert not fail, fail"` → no output

If any step fails, the implementation is rolled back via `git reset --hard`
and the SDD is paused for diagnosis. The source repo is **never** touched.

## 3. Privacy / public-repo hygiene scan (RK-1 mitigation)

Before commit, run a leak-scan over the lifted tree (Step 5 deliverable).
Specifically:

| Check | Pattern | Action |
|-------|---------|--------|
| Hostnames | `home.hont.ro`, `nas.hont.ro`, `*.local`, anything in `~/.ssh/config` Host entries | Redact in test fixtures; flag in source code (should not be there to begin with) |
| IPs | RFC1918 (`10.*`, `192.168.*`, `172.16-31.*`) | Allowed in **example/comment** form only; flag if appearing as a real default in code |
| API keys / tokens | High-entropy Shannon ≥ 4.0 strings >24 chars in `.py` files | Flag every occurrence; abort if any are real |
| Secrets dotenv-style | `KEY=VALUE` patterns in test fixtures | Flag, redact, or move to dedicated `.env.example` |
| Third-party copyright (AS-2 mitigation) | `Copyright (c)`, `SPDX-License-Identifier`, `License:` headers in `*.py`, mismatched author names | Flag every non-operator copyright header; **abort** the commit until reviewed (the LICENSE relicense in PR #1 only covers the operator's own code) |

Tool: a small Python script written for this SDD only (Step 5 deliverable),
results recorded in `out/Rivet/sdd/phase-0.5-source-lift/leak-scan.json`.

**AS-1 mitigation — hard commit gate:** the Step 5 implementation MUST
refuse to run `git add mcp/` if the leak-scan JSON contains any entry
with `severity: "real-secret"` OR `severity: "non-operator-copyright"`.
The operator must triage them (redact, allowlist with rationale, or
rewrite history) before the commit can proceed. "Implicit human review"
is NOT the gate — the script's exit code is.

## 4. Out of scope (re-stated)

- No `pyproject.toml` metadata edits (no URL change, no version bump, no
  `name` rename). Done in #3 alongside the build workflow.
- No relicense block at the top of `*.py` files. Repo-level MIT `LICENSE`
  applies to all repo content; per-file header comments are a Phase 1+
  cosmetic concern.
- No `mcp/README.md` create — that's a Phase 1+ deliverable.
- No `.dockerignore` or `.github/workflows/` files in this PR. PR #3
  introduces them.

## 5. Open questions deferred to later phases

- Q1 (Phase 1+): When lifting per-server packages, do we keep `mcp/` as a
  legacy folder forever or rename to `legacy-monolith/`? Out of scope.
- Q2 (PR #6): The source repo's deletion is a separate PR after the
  cluster has been running on the new image for ≥ 24h. Out of scope here.

## 12. Test Plan

| Test | Where | Asserts |
|------|-------|---------|
| Hash manifest round-trip | Step 5 post-copy | Each lifted file's SHA-256 matches the source-side hash recorded in .lift-manifest.json |
| Inventory parity (G-2) | 	ools/validate_inventory.py --source-repo C:\src\homelab | Source repo's tool set still 133/29/104 at pinned commit |
| Lift completeness (G-5) | 	ools/verify_lift.py | AST-scan of LIFTED mcp/src/homelab_mcp/server.py produces exactly the 133 names in docs/migration/tool-inventory.json |
| Test suite green | pytest mcp/tests -q from new repo | 102 passed (matches Phase 0 baseline) |
| Leak-scan clean | 	ools/leak_scan.py mcp/ | leak-scan.json has no 
eal-secret or 
on-operator-copyright entries; otherwise the commit is blocked |
| Source repo untouched | git -C C:\src\homelab status -s mcp/ | Empty output before and after Step 5 |

## 13. File Inventory

Files this Step 5 implementation creates or modifies in dragoshont/homelab_mcp:

| Path | Created | Modified |
|------|:------:|:--------:|
| mcp/src/homelab_mcp/*.py | ✓ | |
| mcp/tests/*.py | ✓ | |
| mcp/Dockerfile | ✓ | |
| mcp/pyproject.toml | ✓ | |
| mcp/conftest.py (if present in source) | ✓ | |
| mcp/.lift-manifest.json | ✓ | |
| 	ools/verify_lift.py (G-5 helper) | ✓ | |
| 	ools/leak_scan.py (RK-1/AS-2 helper) | ✓ | |
| out/Rivet/sdd/phase-0.5-source-lift/leak-scan.json (evidence) | ✓ | |

Files explicitly NOT modified by this step:

- Any file in C:\src\homelab\ (the source repo) — read-only.
- Any file in dragoshont/homelab_mcp outside mcp/, 	ools/, and out/Rivet/sdd/phase-0.5-source-lift/ — Phase 0 artifacts and SDD evidence are untouched.
