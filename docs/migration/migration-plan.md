# Homelab MCP Migration Plan

This document is the public-facing migration plan for splitting the existing
monolithic homelab MCP server (133 tools, 29 mutating) into smaller,
domain-scoped MCP servers hosted in this repository.

The authoritative spec, design, contract, and adversarial review live under
`out/Rivet/sdd/homelab-mcp-migration-plan/` (SDD `homelab-mcp-migration-plan`).
This page is the plain-English summary intended to be readable without SDD
context.

## Why split

The monolith currently exposes the entire 133-tool surface to OpenWebUI as a
single object. Read-only inventory tools share a process and NetworkPolicy
with mutating control tools (`flux_*`, `unifi_block`, `kube_restart`, etc.).
Splitting it gives:

- **Smaller model-controllable surface per endpoint.** OpenWebUI sees only the
  tools relevant to a server.
- **Trust-boundary isolation.** Read-only domains do not share a process with
  the mutating control surface.
- **Independent change risk.** A bug in the network server cannot crash media.
- **Open-source readiness.** Community can pull a domain server (e.g.
  `homelab-mcp-platform`) without inheriting house-specific home-automation
  integrations.

## Target split (5 servers)

| Server | Role | Tools | Writes | Source coverage (prefixes) |
|--------|------|------:|------:|----------------------------|
| `homelab-mcp-platform` | readonly | 51 | 0 | kube (RO), host, ansible, backup, image, gitops, flux (RO), audit, cert, dns, homelab, ingress, netdata |
| `homelab-mcp-media` | readonly | 30 | 0 | sonarr (RO), radarr (RO), lidarr (RO), readarr (RO), mylar3, prowlarr (RO), qbt (RO), plex (RO), media, cf |
| `homelab-mcp-network` | readonly | 7 | 0 | unifi (RO) |
| `homelab-mcp-homeauto` | readonly | 16 | 0 | dirigera (RO), homebridge, scrypted, apple (RO) |
| `homelab-mcp-control` | control (opt-in) | 29 | 29 | every domain's mutating tools |
| **Σ** | | **133** | **29** | |

Authoritative per-tool mapping: [`tool-inventory.json`](./tool-inventory.json).

## Phased rollout

1. **Phase 0 (this PR).** Plan, inventory JSON, validator script, append-only
   tracker seeds. No runtime change. No code moved.
2. **Phase 1 — `homelab-mcp-platform`.** Largest read-only surface, lowest
   blast radius. Proves the split mechanism (packaging, transport,
   registration, naming) on safe surfaces. Includes the empirical OpenWebUI
   tool-name overlap test.
3. **Phase 2 — `homelab-mcp-media`.** Second largest, fully read-only.
4. **Phase 3 — `homelab-mcp-network` and `homelab-mcp-homeauto` in parallel.**
   Both small, both depend on a locked `homelab-mcp-core` version pinned
   for the phase.
5. **Phase 4 — `homelab-mcp-control`.** Last; mutating; opt-in connection from
   OpenWebUI. Requires NetworkPolicy + label gate + bearer token from K8s
   Secret.
6. **Phase 5 — Monolith decommission.** Only after all five split servers
   have entries in [`phase-status.json`](./phase-status.json) showing all
   acceptance gates green AND the source repo's OpenWebUI config no longer
   references the monolith URL.

The existing `homelab-mcp-proxy:1.1.0` deployment stays running through
phases 0–4 as the always-available fallback.

## Acceptance gates per server

Each split server must pass all five gates before its phase ends:

- **G1 Inventory parity** — registered tool name set equals the assigned
  subset from `tool-inventory.json`.
- **G2 Readonly enforcement** — no `WRITE_TOOLS` member registered on a
  readonly server; for control, no readonly tool registered.
- **G3 Smoke** — `/docs` and `/openapi.json` return 200 from a pod labeled
  `app.kubernetes.io/name=open-webui`; openapi lists exactly the expected
  tool count.
- **G4 Side-by-side parity** — read-only tools: same JSON shape from
  monolith and split. Control tools: byte-for-byte equality of the rendered
  downstream request payload (no live mutation).
- **G5 Network isolation** — RO servers' NetworkPolicy denies non-OpenWebUI
  ingress; control adds a second label gate.

## Trust-boundary commitments

- Read-only servers ship by default; control server ships last and is only
  registered in OpenWebUI when the operator explicitly opts in.
- Control server requires NetworkPolicy + a second label gate
  (`mcp.homelab/control-allowed=true`) on the OpenWebUI pod **and** an
  app-layer bearer token sourced from a K8s Secret.
- Audit logging is preserved unchanged (lifted into `homelab-mcp-core`); the
  per-server vs shared sink decision is made in Phase 1 (open question Q5).

## What this PR does not do

- No tool source code is moved.
- No container image is built or published.
- No OpenWebUI endpoint is added or removed.
- No file in the source repo `dragoshont/homelab` is modified.

## Follow-ups (separate PRs, after this one merges)

- Source-repo CI guard against new `@mcp.tool` decorators during phases 1–4
  (lands in `dragoshont/homelab`, not here).
- Hidden-mutation heuristic scanner (Phase 1 SDD).
- OpenWebUI tool-name overlap empirical test (Phase 1 SDD first deliverable).
- Audit-sink topology decision (Phase 1 SDD; Q5 in design.md §11).

## Where to read more

- [`out/Rivet/sdd/homelab-mcp-migration-plan/spec.md`](../../out/Rivet/sdd/homelab-mcp-migration-plan/spec.md) — full PRD.
- [`out/Rivet/sdd/homelab-mcp-migration-plan/design.md`](../../out/Rivet/sdd/homelab-mcp-migration-plan/design.md) — architecture, gates, schemas, test plan, file inventory.
- [`out/Rivet/sdd/homelab-mcp-migration-plan/contract.md`](../../out/Rivet/sdd/homelab-mcp-migration-plan/contract.md) — verify contract (MUST PASS / MUST FAIL).
- [`out/Rivet/sdd/homelab-mcp-migration-plan/as-findings.json`](../../out/Rivet/sdd/homelab-mcp-migration-plan/as-findings.json) — adversarial review (AS-1..14).
