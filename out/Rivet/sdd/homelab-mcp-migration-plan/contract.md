# Verify Contract — homelab-mcp-migration-plan

**Workflow:** `homelab-mcp-migration-plan` (#0)
**Repo:** `dragoshont/homelab_mcp` (public)
**Branch:** `feat/migration-plan`
**Scope of this SDD:** the *plan* for migrating the existing monolithic homelab MCP into smaller, domain-scoped MCP servers hosted in this repository. No tool source code is moved in this PR.

---

## MUST PASS

The PR is acceptable only when **all** of the following are true:

1. **MP-1 Inventory fidelity.** A machine-readable inventory of the source monolith
   exists in this repo at `docs/migration/tool-inventory.json` and contains
   exactly the **133** tool names currently registered in
   `C:\src\homelab\mcp\src\homelab_mcp\server.py` and exactly the **29**
   write-tool names in `C:\src\homelab\mcp\src\homelab_mcp\policy.py:WRITE_TOOLS`.
   The two counts MUST be reproduced verbatim, not summarized.
2. **MP-2 Total domain coverage (set equality, not just count).** Every one of
   the 133 tools is assigned to exactly one target split server in the plan.
   `set(per_server_tools) == set(source_tools)` (the inventory's tool name
   set equals the source repo's tool name set). The intersection of any two
   server tool sets is empty. Cardinality match alone is insufficient — a
   gate that only sums counts can mask a duplicate-and-omission pair.
3. **MP-3 Write-tool isolation.** No write-tool from the 29-name set is placed on
   a server whose declared role is `readonly`. Every write-tool lives on a
   server whose declared role is `control` (mutating opt-in).
4. **MP-4 Phased rollout with explicit gates.** The plan declares ordered phases.
   Phase N+1 cannot start until Phase N's split server passes a named
   acceptance gate (inventory parity, readonly enforcement, smoke).
5. **MP-5 Compatibility monolith retained.** The plan keeps the existing
   `homelab-mcp-proxy:1.1.0` container running and connected to OpenWebUI
   until at least one split server is in production and validated. Plan
   includes an explicit cutover checklist per server, not a big-bang switch.
6. **MP-6 Adversarial review clean.** Step 4 (`as-findings.json`) has zero
   `severity: critical` findings. `severity: high` findings either have a
   recorded mitigation in `design.md` or an explicit accepted-risk entry.
7. **MP-7 No source-repo writes from this SDD.** This SDD touches only files in
   `C:\src\homelab_mcp`. The source `C:\src\homelab` working tree is
   untouched by anything in this workflow.
8. **MP-8 Doc artifact present.** `docs/migration/migration-plan.md` exists on
   `feat/migration-plan` with the same target-split table that appears in
   `design.md`, so a reader of the public repo (without SDD context) can
   understand the plan.
9. **MP-9 Verify gate.** `rivet verify --scope branch` exits 0 with no critical
   adversarial findings on the diff.

## MUST FAIL

Reject the PR if any of the following hold:

1. **MF-1** Per-server tool counts sum to anything other than 133, or any tool name
   appears on more than one server, or any tool name in the inventory is not
   one of the 133 source tools.
2. **MF-2** Any one of the 29 write-tool names appears on a server whose role is
   `readonly`, OR any write-tool is unassigned to any control server.
3. **MF-3** Plan proposes deleting, replacing, or in-place rewriting the source
   monolith before any split server has passed its acceptance gate.
4. **MF-4** Plan proposes a single big-bang cutover that swaps the OpenWebUI
   connection from monolith to splits in one step.
5. **MF-5** Plan proposes shipping any control/mutating server without subnet/auth
   isolation distinct from the readonly servers' transport.
6. **MF-6** `as-findings.json` contains `severity: critical` findings that are not
   resolved or explicitly accepted with rationale.
7. **MF-7** SDD content artifacts in `out/Rivet/sdd/homelab-mcp-migration-plan/` are missing
   any of: `context.json`, `contract.md`, `spec.md`, `design.md`,
   `as-findings.json`. (`verify-result.json`, `build-result.json`,
   `contract-grade.json`, and `f10-compliance.md` are produced by their
   respective SDD steps and are not preconditions for this Phase 0 PR;
   their absence pre-Step-9/10 does not trigger MF-7.)
8. **MF-8** The PR modifies any file under `C:\src\homelab\` (the source repo).
9. **MF-9** The PR makes any unannotated claim about a tool's "verified working"
   status. Verification status is permitted only for tools backed by smoke
   evidence stored in `docs/migration/verification/`.

## Integration Points

| Touchpoint | Direction | Contract |
|------------|-----------|----------|
| Source repo `C:\src\homelab` | **read-only** | This SDD reads `mcp/src/homelab_mcp/server.py`, `policy.py`, `Dockerfile`, `apps/platform/mcp-proxy/deployment.yaml`. No writes. |
| Tool inventory (133) | input → fixed | Pulled from `server.py` AST decorators (`@mcp.tool(...)`). Snapshot frozen in `docs/migration/tool-inventory.json`. |
| Write-tool set (29) | input → fixed | Pulled verbatim from `policy.py:WRITE_TOOLS`. Used to enforce MUST-FAIL #2. |
| `homelab-mcp-proxy:1.1.0` deployment | runtime peer | Stays running during entire migration. Plan declares it the canonical fallback. |
| OpenWebUI MCP connection | runtime peer | Currently `http://homelab-mcp-proxy.default.svc.cluster.local:8080/openapi.json`. Plan declares per-server endpoints to be added alongside, not replacing, the monolith URL. |
| MCP transport choice | design constraint | stdio for local single-client; Streamable HTTP for shared/remote, with auth + Origin validation, per the architecture research the plan cites. |
| `feat/migration-plan` branch | output | All SDD artifacts + `docs/migration/*` land here. Branched from `origin/main`. |
| Future SDDs (per split server) | downstream | This SDD declares the named acceptance gate each future SDD must pass. Future SDDs reference this `contract.md` as their input contract. |

## Out of scope (explicit non-goals)

- Moving any tool implementation source code in this PR.
- Building or publishing any container image for a split server.
- Wiring any new MCP endpoint into OpenWebUI.
- Decommissioning the monolith.
- Adding new tools, refactoring tool internals, or changing audit/policy semantics.
