# Spec — homelab-mcp-migration-plan

**Workflow:** `homelab-mcp-migration-plan` (#0)
**Repo:** `dragoshont/homelab_mcp` (public)
**Status:** Step 3 (Spec/PRD)
**Audience:** technical reviewers without prior context on this homelab MCP.

---

## 1. Executive summary

The homelab MCP is currently a single FastMCP Python server (`homelab_mcp.server`)
that registers **133 tools** across 28 functional prefixes (kubernetes, media
stack, networking, home automation, observability, etc.). Of these, **29** are
mutating ("write") tools as enforced by `homelab_mcp.policy.WRITE_TOOLS`. The
monolith is wrapped by `mcpo` and exposed to OpenWebUI as a single
`homelab-mcp-proxy:1.1.0` Kubernetes Deployment, currently fronted by a
NetworkPolicy that allows ingress only from OpenWebUI.

This is operationally fine but architecturally over-coupled for an open-source
release: OpenWebUI sees the entire 133-tool surface as a single
model-controllable object, every tool change risks every other tool, and the
monolith mixes read-only observation with mutating control on one transport.

This SDD is the **migration plan** (not the migration itself). It defines:

- the **target split** of the monolith into 4 read-only domain servers + 1
  opt-in control server;
- the **acceptance gate** each split server must pass before its phase ends;
- the **phased rollout** that keeps the existing monolith running and connected
  to OpenWebUI throughout, with no big-bang cutover;
- the **artifacts** that land in this PR (`contract.md`, `spec.md`, `design.md`,
  `as-findings.json`, plus `docs/migration/*` for public consumption).

Tool source code is **not** moved in this PR.

## 2. Background and motivation

### 2.1 Current state (verified 2026-04-28)

| Property | Value | Source |
|---|---|---|
| Tool count | 133 | AST scan of `mcp/src/homelab_mcp/server.py` |
| Write-tool count | 29 | `mcp/src/homelab_mcp/policy.py:WRITE_TOOLS` |
| Domain prefixes | 28 | distinct first-underscore-segment names |
| Largest prefix | `kube` (20 tools) | inventory |
| Smallest prefix | 8 prefixes with 1 tool each | inventory |
| Container | `homelab-mcp-proxy:1.1.0`, `imagePullPolicy: Never` | `apps/platform/mcp-proxy/deployment.yaml` |
| OpenWebUI link | `http://homelab-mcp-proxy.default.svc.cluster.local:8080/openapi.json` | OpenWebUI MCP config |
| Build pipeline | none — image built manually on host | confirmed prior session |
| Tests | 102 passing (`mcp/tests/`) | `pytest mcp/tests -q` (Phase 0 baseline 2026-05-01) |

### 2.2 Problems with the monolith

1. **Surface explosion.** OpenWebUI exposes 133 tools to the model. Even with
   tool filtering, the registered surface is the entire homelab. A prompt
   injection in any input the model reads can attempt any of 133 actions.
2. **Mutating + reading on one transport.** Read-only inventory tools share an
   image, a process, and a NetworkPolicy with mutating control tools (`flux_*`,
   `unifi_block`, `kube_restart`, `apple_*`, `dirigera_set_*`).
3. **Coupled change risk.** Adding a new media-stack tool requires re-rolling
   the whole monolith image, which also serves networking and kubernetes.
4. **Hard to share.** Some domains (media, kube observation) are reusable by
   the broader open-source community; others (homeauto with house-specific
   integrations, control with destructive actions) are not. The monolith
   ships them together.

### 2.3 Why now

The monolith was deployed and verified end-to-end last session. Tests pass.
Inventory is stable. This is the cleanest moment to plan the split before
adding more tools that would deepen the coupling.

## 3. Goals (non-functional)

| Goal | Metric |
|------|--------|
| Inventory parity | 133 = sum of per-server tool counts; 0 unassigned, 0 duplicated |
| Write isolation | All 29 write-tools on the control server; 0 on any readonly server |
| No-regression rollout | Monolith stays up the entire migration; no OpenWebUI outage |
| Per-domain independence | A bug in any single split server cannot crash any other split |
| Reviewability | A reader of the public repo can read `docs/migration/migration-plan.md` and understand the plan without Rivet/SDD context |
| Open-source readiness | Each split server can be packaged independently as a container/PyPI distribution |

## 4. Non-goals

- Moving tool source in this PR.
- Building or publishing any container image for a split server.
- Decommissioning the monolith.
- Adding new tools, refactoring tool internals, or changing audit/policy semantics.
- Designing a new MCP gateway/aggregator layer (out of scope for this plan;
  may follow as a separate SDD).
- Cross-cluster or remote MCP transport hardening (the existing in-cluster
  NetworkPolicy + cluster auth are kept as the boundary).
- Source-repo CI guard against new `@mcp.tool` decorators during the
  migration window (RK-5 mitigation). This SDD cannot enforce that from the
  target repo; it ships as a **separate PR in `dragoshont/homelab` after
  this PR merges** and is tracked in `migration-plan.md` as a follow-up.

## 5. Users and use cases

| User | Use case | What changes for them |
|------|----------|------------------------|
| Operator (you) | Run `flux_reconcile`, `kube_restart`, etc. via OpenWebUI | After migration, OpenWebUI shows only the readonly domain servers by default; the control server is connected explicitly when mutating actions are needed. |
| Operator | Read-only inventory of media stack | Goes through `homelab-mcp-media`, smaller surface, faster. |
| Open-source consumer | Pull `homelab-mcp-platform` container, point at their cluster | Possible after split; not possible today. |
| Future contributor | Add a new media tool | Touches only `homelab-mcp-media` repo path and image; cannot break kube/unifi. |

## 6. Requirements

### 6.1 Functional

- **R1 — Inventory.** `docs/migration/tool-inventory.json` lists all 133 tool
  names with their assigned target server and a `mutating: true|false` flag
  derived from `WRITE_TOOLS`. Sum check: `len(tools) == 133`,
  `len(mutating_true) == 29`.
- **R2 — Target split.** `docs/migration/migration-plan.md` contains the
  same per-server mapping table as `design.md` §4.
- **R3 — Per-server acceptance gate.** Each target server has a named gate
  (`G1..G5`, see design §6) that must pass before its phase ends.
- **R4 — Phasing.** Phase order is platform → media → network/homeauto →
  control. No phase advances until prior phase's gate passes.
- **R5 — Compatibility window.** During every phase the monolith deployment
  remains running and connected to OpenWebUI alongside any partial split.
- **R6 — Cutover checklist.** Each server has a documented per-server
  cutover checklist (add OpenWebUI endpoint → verify side-by-side → remove
  monolith's coverage of that domain only after gate passes).
- **R7 — Decommission gate.** Monolith is removed only after all 4 readonly
  servers + the control server have passed their gates and OpenWebUI no
  longer references the monolith URL.

### 6.2 Non-functional

- **NF1 — Atomicity per phase.** Each phase is its own future SDD with its own PR.
- **NF2 — Reversibility.** Any phase can be rolled back by un-registering its
  OpenWebUI endpoint; the monolith is the always-available fallback.
- **NF3 — Public repo hygiene.** No homelab IPs, hostnames, secret values,
  or topology specifics in this repo. Plan refers to clusters/services
  symbolically; concrete deployment configs stay in the private homelab repo.

### 6.3 Constraints

- **C1 — No source-repo writes.** This SDD reads `C:\src\homelab` only.
- **C2 — Stable inventory snapshot, with explicit re-pin path.** The 133/29
  numbers are frozen in `tool-inventory.json` from the source repo at commit
  `0727116cc8217994bbb1a8d083bc95140671a580` (current `homelab/main`). If
  the source repo changes before the inventory is captured, the snapshot
  records the actual commit it was taken from. **Re-pinning is permitted at
  the start of any phase SDD** (e.g., to absorb a security update on the
  monolith). A re-pin MUST: (a) diff the old vs new tool name sets,
  (b) record the diff as an entry in `docs/migration/inventory-history.json`,
  (c) update `tool-inventory.json` atomically with the new snapshot.
  Set-equality enforcement (contract MUST-PASS #2) re-runs against the new
  snapshot.
- **C3 — Transport.** stdio for local single-client; Streamable HTTP behind
  cluster auth for shared/in-cluster.

## 7. Success criteria for this PR

(Identical to `contract.md` MUST PASS, restated for the PRD reader.)

1. `docs/migration/tool-inventory.json` exists and reproduces 133/29 verbatim.
2. Per-server tool counts in `design.md` and `docs/migration/migration-plan.md` sum to 133, with no overlap.
3. No write-tool is on any readonly server; all 29 writes are on the control server.
4. Phased rollout with per-phase acceptance gate is documented.
5. Compatibility monolith retention is documented.
6. `as-findings.json` has 0 critical findings.
7. `rivet verify --scope branch` exits 0.

## 8. Risks (high-level; design.md §8 has mitigations)

| ID | Risk | Severity |
|----|------|----------|
| RK-1 | Inventory drift between snapshot and live source repo | Medium |
| RK-2 | A "readonly" tool actually mutates state under the hood (audit/cert tools) | Medium |
| RK-3 | OpenWebUI tool-naming collisions when both monolith and split server are connected | High |
| RK-4 | Control server's auth boundary weaker than the monolith's NetworkPolicy | High |
| RK-5 | Plan rendered immediately stale by adding tools to the monolith mid-migration | Medium |
| RK-6 | Split servers diverging on shared helpers (ssh client, http client) | Medium |
| RK-7 | Public repo accidentally leaks homelab specifics from copy-paste | High |
