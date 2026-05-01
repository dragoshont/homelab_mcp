# Spec — phase-0.5-source-lift

**Workflow:** `phase-0.5-source-lift` (#0)
**Repo:** `dragoshont/homelab_mcp` (public)
**Branch:** `feat/phase-0.5-source-lift` (from `origin/main` after PR #1 merged)
**Predecessor SDD:** `homelab-mcp-migration-plan` (Phase 0, merged in #1)

---

## 1. Executive summary

Phase 0 produced the architecture plan and consolidated it on `main` of
`dragoshont/homelab_mcp`. Phase 0.5 is the **smallest possible step** that
makes the new repo functionally useful: copy the existing 133-tool MCP
monolith Python sources from the private `dragoshont/homelab` repo into the
new public repo, **without code changes**, and prove the lifted code still
produces the same tool inventory and passes the same tests.

This SDD does **not** introduce the architecture split (Phase 1+) and does
not change any deployment or build pipeline (PR #3+). The single deliverable
is a byte-identical copy of `mcp/` with the test suite green.

## 2. Background

| Property | Value | Source |
|---|---|---|
| Source repo | `dragoshont/homelab` (private) | git remote |
| Source path | `mcp/src/homelab_mcp/` + `mcp/tests/` + `mcp/Dockerfile` + `mcp/pyproject.toml` | inspection |
| Source pinned commit | `0727116cc8217994bbb1a8d083bc95140671a580` | Phase 0 inventory |
| Tool count | 133 | `tools/validate_inventory.py` (Phase 0) |
| Write-tool count | 29 | `mcp/src/homelab_mcp/policy.py:WRITE_TOOLS` |
| Test count | 102 | `pytest mcp/tests -q` |
| Container in cluster | `homelab-mcp-proxy:1.1.0`, `imagePullPolicy: Never` | `apps/platform/mcp-proxy/deployment.yaml` |
| Container uptime | 4d17h, 3 restarts | preflight |
| OpenAPI paths | 133 | preflight smoke against running pod |

## 3. Goals

| Goal | Metric |
|------|--------|
| **G-1 Byte-identical lift** | Every file in `mcp/` of this repo has SHA-256 equal to the corresponding file in `homelab` repo at the pinned commit. No edits. |
| **G-2 Inventory preserved** | `python tools/validate_inventory.py --source-repo C:\src\homelab` continues to exit `0` after the lift |
| **G-3 Tests green** | `pytest mcp/tests -q` from the new repo prints `102 passed` |
| **G-4 No accidental cross-repo coupling** | `mcp/` in the new repo has no `import` from the source repo's non-`mcp/` modules and no path-based references to `C:\src\homelab` |

## 4. Non-goals

- Refactoring tool internals.
- Architecture split into 5 servers (Phase 1+).
- Building the image or pushing it anywhere (PR #3).
- Removing `mcp/` from the source repo (PR #6, after 24h soak).
- Updating `homelab` repo's deployment to point at a new image (PR #5).
- Per-server READMEs / docs / per-tool documentation lift.
- License metadata changes inside `mcp/pyproject.toml` (lifted as-is).
- History preservation (decision: fresh history, not `git filter-repo`).

## 5. Users

| User | Use case | What changes |
|------|----------|--------------|
| Operator (you) | Trust that the lifted code is identical to what the cluster runs today | After this PR merges, `dragoshont/homelab_mcp` contains the actual MCP code, not just the plan |
| Reviewer | Review a small, low-risk byte-copy PR | Diff is purely additive; no semantic changes anywhere |
| Phase 1 SDD | Have a real codebase to refactor against | Source-of-truth becomes this repo; Phase 1+ branches off `main` of `dragoshont/homelab_mcp` |

## 6. Requirements

### 6.1 Functional

- **R1.** Copy `mcp/src/homelab_mcp/*.py` (all `.py` files at any nesting) to `mcp/src/homelab_mcp/` in this repo.
- **R2.** Copy `mcp/tests/*.py` and `mcp/tests/conftest.py` (if present) to `mcp/tests/` in this repo.
- **R3.** Copy `mcp/Dockerfile` to `mcp/Dockerfile` in this repo.
- **R4.** Copy `mcp/pyproject.toml` to `mcp/pyproject.toml` in this repo.
- **R5.** Copy `mcp/conftest.py` if present at `mcp/`.
- **R6.** Add `mcp/` to nothing in `.gitignore`; the lift is committed.
- **R7.** SHA-256 manifest at `mcp/.lift-manifest.json` recording each file's hash and the source-repo commit it was lifted from. Used by Phase 1+ to detect drift if the source repo changes before #6 deletes `mcp/` there.

### 6.2 Non-functional

- **NF1.** This SDD makes **zero edits** to source-repo `C:\src\homelab\`. (SDD invariant from Phase 0.)
- **NF2.** Every Python file is verified byte-identical via SHA-256 before commit.
- **NF3.** No new tools are added; no `WRITE_TOOLS` changes; no `policy.py` changes.

### 6.3 Constraints

- **C1 — Fresh history.** Per operator's decision (1=no), `git filter-repo` is **not** used. Files are added as a single new commit.
- **C2 — Source pinned commit.** Lift is taken from `homelab` HEAD at SDD-init time, recorded in `.lift-manifest.json`. If source repo HEAD has moved, the lift records the new SHA but compares only against that fresh snapshot.
- **C3 — Public repo hygiene.** Any test fixture that contains a real homelab hostname / API key / token / IP MUST be flagged in the adversarial spec step before commit. Decision: redact-or-skip-or-leave handled in Step 4.
- **C4 — License compatible.** Source repo has no `LICENSE`. The lifted code lands in a repo with an MIT `LICENSE`. Operator (sole author of the source code) authorizes the relicense to MIT for the lifted files. This SDD records that explicitly.

## 7. Risks

| ID | Risk | Severity |
|----|------|----------|
| RK-1 | Source files contain hardcoded homelab specifics (hostnames, IPs, tokens) that should not be public | **High** |
| RK-2 | Source repo has moved between Phase 0 commit-pin and this lift; the lifted code differs from what's in the cluster | Medium |
| RK-3 | Test fixtures depend on environment that doesn't exist in the new repo's CI (none yet, but #3 will add one) | Low — no CI runs in this PR |
| RK-4 | `pyproject.toml` references the source repo's URL or has metadata that needs updating | Low — fix in same PR if found |
| RK-5 | `mcp/Dockerfile` references a base image or path that breaks once moved | Low — same Dockerfile, same `pip install .`, no path-based context |
| RK-6 | An imported helper from `homelab_mcp.*` actually lives outside `mcp/src/homelab_mcp/` in the source repo | Medium — would surface as ImportError in Step 7 |
| RK-7 | The source repo's git history (which we are NOT preserving per decision #1) contains attribution that gets lost | Low — operator is sole author |

## 8. Success criteria for this PR / Acceptance Criteria

### MUST PASS

1. `mcp/` exists in this repo with files matching SHA-256 of source.
2. `mcp/.lift-manifest.json` records source commit + per-file hashes.
3. `python tools/validate_inventory.py --source-repo C:\src\homelab` exits 0.
4. `cd mcp && python -m pytest tests -q` prints `102 passed`.
5. Adversarial spec step (Step 4) returns 0 critical findings.
6. `rivet verify --scope branch` exits 0 with no critical adversarial findings.
7. No homelab-private values (real IPs/hostnames/tokens/keys) appear in any committed file.
8. `tools/verify_lift.py` (G-5) confirms the LIFTED `mcp/src/homelab_mcp/server.py` AST-scans to the same tool set as `docs/migration/tool-inventory.json`.

### MUST FAIL

Reject the PR if any of:

1. Any lifted file's SHA-256 does not match the source-repo file at the pinned commit.
2. The leak-scan reports a `real-secret` or `non-operator-copyright` finding that is not triaged.
3. The PR modifies any file under `C:\src\homelab\` (the source repo).
4. `mcp/.lift-manifest.json` is missing or its hashes do not reproduce on re-check.
5. `pytest mcp/tests` fails or reports fewer than 102 passing tests.

