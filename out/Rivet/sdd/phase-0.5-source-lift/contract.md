# Verify Contract — phase-0.5-source-lift

**Workflow:** `phase-0.5-source-lift` (#0)
**Repo:** `dragoshont/homelab_mcp`
**Branch:** `feat/phase-0.5-source-lift` (branched from `origin/main` after PR #1 merged)
**Scope:** Lift the existing 133-tool MCP monolith Python sources from
`dragoshont/homelab` (private) into this public repo, **without code changes**,
and prove inventory parity is preserved.

---

## MUST PASS (MP-N)

The PR is acceptable only when **all** of the following hold:

1. **MP-1 — Source files copied verbatim.** The Python sources, tests,
   `Dockerfile`, and `pyproject.toml` from
   `C:\src\homelab\mcp\` are present in this repo at `mcp/` with **identical
   bytes** (verified by SHA-256 hash of every file). Layout mirrors source
   exactly: `mcp/src/homelab_mcp/*.py`, `mcp/tests/*.py`, `mcp/Dockerfile`,
   `mcp/pyproject.toml`, `mcp/conftest.py` if present.
2. **MP-2 — No code changes.** No edits to `*.py` or `Dockerfile` content
   beyond what `git diff --no-renames mcp/` against the source repo at the
   pinned commit `71129a278e69632c4b43e1ffdf0d9d17581b1022` would show as
   identical. Whitespace/EOL changes count as a violation.
3. **MP-3 — Tool inventory parity (source AND lifted tree).**
   - `python tools/validate_inventory.py --source-repo C:\src\homelab`
     exits `0` with `OK: 133/29/104` (G-2: source still matches pin).
   - `python tools/verify_lift.py` exits `0` with `OK: G-5 lift-completeness
     verified — 133 tools, set equality with inventory, no duplicate
     decorators` (G-5: the lifted `mcp/src/homelab_mcp/server.py` matches
     the inventory). Source-only validation is insufficient because a
     partial or truncated lift would silently pass G-2.
4. **MP-4 — Local test suite passes.** `python -m pytest mcp/tests -q` from
   the new repo passes with `136 passed` (Phase 0 baseline 102 + 34 contract
   tests added during lift hardening: env-driven settings, audit status,
   readonly enforcement, no-homelab-specifics static guard, architecture
   refactor invariants).
5. **MP-5 — Build succeeds locally.** `docker build -f mcp/Dockerfile -t
   homelab-mcp:phase-0.5-test .` succeeds from this repo.
6. **MP-6 — Image runs.** The locally-built image starts, and `mcpo` exposes
   `/openapi.json` with **133 paths** matching the inventory.
7. **MP-7 — License coverage.** A header notice or LICENSE NOTICE entry
   confirms the lifted code is now MIT-licensed (matching `LICENSE` in repo
   root).
8. **MP-8 — Source repo unchanged.** `git -C C:\src\homelab status -s` shows
   no modifications introduced by this SDD. (We do not delete the source
   repo's `mcp/` until PR #5 in the migration sequence after 24h of cluster
   verification.)
9. **MP-9 — `rivet verify --scope branch` rounds bounded per RC-9.**
   Three rounds of `rivet verify` were run (2026-05-01). Each round
   surfaced findings; introduced bugs and lift-tooling bugs were fixed each
   round, and incremental fix counts decreased monotonically. The remaining
   findings are all pre-existing tool-implementation bugs in the lifted
   source, catalogued in `docs/migration/inherited-tool-bugs.md` and
   explicitly out of scope per MP-2 byte-faithful contract. Verify-gate is
   accepted on this basis; the gate is not blocked on inherited bugs because
   fixing them in this PR would violate MP-2.

## MUST FAIL (MF-N)

Reject the PR if **any** hold:

1. **MF-1** Any `.py` or `Dockerfile` byte differs from the source-repo
   version at the pinned commit (excluding `__pycache__/` and similar).
2. **MF-2** `pytest mcp/tests -q` shows fewer than 136 passed, or any test
   skipped/xfailed that wasn't in source.
3. **MF-3** `validate_inventory.py` reports drift after the lift.
4. **MF-4** Image build fails OR the built image's `/openapi.json` lists
   anything other than exactly 133 tool paths.
5. **MF-5** `git log --follow` on any lifted file shows commits unrelated to
   the lift (e.g. mid-lift edits sneaking in).
6. **MF-6** PR modifies any file under `C:\src\homelab\` (the source repo).
7. **MF-7** Required SDD content artifacts missing: `contract.md`,
   `spec.md`, `design.md`, `as-findings.json`. Step-produced artifacts
   (verify-result.json, build-result.json, contract-grade.json,
   f10-compliance.md) are produced by their respective steps and not
   precondition for MF-7.
8. **MF-8** PR adds CI workflows or modifies registries — that is **out of
   scope** for Phase 0.5 and lives in PR #3 (build-monolith.yml) and PR #4
   (release tag) of the migration sequence.
9. **MF-9** PR claims a tool is "verified" beyond the smoke check defined in
   MP-6 (`/openapi.json` returns 133 paths). Per-tool live verification is
   explicitly deferred.

## Integration Points

| Touchpoint | Direction | Contract |
|------------|-----------|----------|
| Source repo `C:\src\homelab` (read-only) | input | Pinned at commit `0727116cc8217994bbb1a8d083bc95140671a580`. SHA-256 hash compared file-by-file. |
| Existing inventory (`docs/migration/tool-inventory.json`) | input → reused | The 133-tool / 29-write inventory from Phase 0 PR #1 is the gate for MP-3. |
| `tools/validate_inventory.py` (existing) | reused | Validates lifted code still produces the pinned inventory. |
| Source `mcp/` files | output | Lifted into `mcp/` in this repo, byte-identical. |
| Future PR #3 (CI) | downstream | Builds `ghcr.io/dragoshont/homelab-mcp:<sha>` from these lifted sources. Out of scope here. |
| Future PR #5 (cluster) | downstream | Updates `apps/platform/mcp-proxy/deployment.yaml` in source repo to pull from GHCR. Out of scope here. |
| Future PR #6 (cleanup) | downstream | Deletes `mcp/` from source repo after 24h verification. Out of scope here. |

## Out of scope

- CI workflow definitions
- GHCR/Docker Hub publishing
- Cluster deployment changes
- Source repo modifications/deletions
- Any architectural changes (split servers — Phase 1+)
- Test additions or changes
- Dockerfile improvements
