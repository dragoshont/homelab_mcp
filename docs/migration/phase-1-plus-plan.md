# Phase 1+ master plan

> **Status:** plan only. Spec for Phase 1.0 (refactor) is below. Each
> subsequent phase ships under its own SDD before merging.

## Goal

Split today's 133-tool monolith image into 5 domain-scoped servers + 1
bundle, along the trust boundaries already encoded in
[`docs/migration/tool-inventory.json`](tool-inventory.json):

| Server | Tools | Mutating |
|---|---:|---:|
| `homelab-mcp-platform` | 51 | 0 |
| `homelab-mcp-media` | 30 | 0 |
| `homelab-mcp-network` | 7 | 0 |
| `homelab-mcp-homeauto` | 16 | 0 |
| `homelab-mcp-control` | 29 | **29** (the only mutating endpoint) |
| `homelab-mcp-bundle` | 133 | 29 (drop-in replacement for today's monolith) |

Set-equality with the current 133 / 104 / 29 totals is enforced by
`tools/validate_inventory.py` (G-2) and `tools/verify_lift.py` (G-5)
on every change.

## Sequencing

| Phase | Deliverable | Complexity | Cluster impact |
|---|---|---|---|
| **1.0** | Refactor `server.py` into per-domain modules; same package, same image, same image tag (still `:latest` from monolith pipeline). Inventory unchanged. Each domain module exposes a `register(mcp)` function. | High (3.3k-line refactor) | None |
| 1.1 | First per-domain server: `homelab-mcp-network` (7 tools). New console script + Dockerfile + CI publish. New image `ghcr.io/dragoshont/homelab-mcp-network:v0.1.0`. | Low (smallest domain proves the pattern) | None |
| 1.2 | `homelab-mcp-homeauto` (16, read-only). | Low | None |
| 1.3 | `homelab-mcp-media` (30, read-only). | Medium | None |
| 1.4 | `homelab-mcp-platform` (51, read-only). | Medium | None |
| 1.5 | `homelab-mcp-control` (29, mutating). Trust-boundary justification for the whole split. Bearer-token auth in addition to network isolation. | High (security review) | None |
| 1.6 | `homelab-mcp-bundle` config-driven multiplexer — runs any subset of the five via YAML config. | Medium | None |
| 1.7 | Cluster cutover: `apps/platform/mcp-proxy/deployment.yaml` switches from the monolith to the bundle (or to per-domain if topology demands). | Low | One PR + one Flux reconcile |

Total: 8 SDDs. Each gets its own spec/design/contract/build/verify
artifacts and merges as a single PR.

## Phase 1.0 — `server.py` refactor (this SDD)

### Spec

Restructure `mcp/src/homelab_mcp/server.py` so each of the 133 tools
lives in a domain-scoped module while the monolith image still ships
the union of all five.

#### Why

Phase 1.1+ extracts each domain into its own server with its own image
and trust boundary. Doing that on top of a 3.3k-line single file means
every per-domain SDD has to do "find the tools, slice them out, fix
imports, fix tests" plus the actual extraction work. That's 5x risk.

Doing the refactor once now means each per-domain SDD is a thin
wrapper: a 30-line entry point that imports
`homelab_mcp.tools.{domain}.register(mcp)` and a Dockerfile.

#### Contract

1. **MP-1 — same tool set.** `python tools/verify_lift.py` exits 0
   with `OK: 133 tools, set equality with inventory, no duplicate
   decorators` after the refactor.
2. **MP-2 — same writes.** `WRITE_TOOLS` in `policy.py` unchanged
   (29 entries).
3. **MP-3 — same image surface.** A locally-built image from the
   refactored sources serves `/openapi.json` with exactly 133 paths.
4. **MP-4 — tests still green.** `pytest mcp/tests -q` reports 145
   passed (or more if regression tests are added).
5. **MP-5 — every per-domain module is self-contained.** Each
   `tools/{domain}.py` imports only from `homelab_mcp.{audit,clients,
   policy,settings}` (the "core" modules) plus stdlib / third-party.
   No tool function lives outside `tools/{domain}.py`.
6. **MP-6 — orchestrator is small.** The new `server.py` is < 80
   lines (5 imports + 5 `register()` calls + main entry).

#### Design

Layout:

```
mcp/src/homelab_mcp/
├── __init__.py
├── app.py                  ← create_mcp() factory (unchanged)
├── audit.py                ← audit logger (unchanged)
├── clients.py              ← Servarr/Qbt/Plex/Homebridge/Mylar3 wrappers (unchanged)
├── main.py                 ← entry script (unchanged)
├── policy.py               ← WRITE_TOOLS + check_readonly (unchanged)
├── registry.py             ← AST tool discovery for tests (unchanged)
├── settings.py             ← env contract (unchanged)
├── server.py               ← orchestrator: ~60 lines (new)
└── tools/
    ├── __init__.py
    ├── platform.py         ← 51 tools (Kubernetes, host, FluxCD, image, CF DNS, Netdata, Ansible, audit)
    ├── media.py            ← 30 tools (Sonarr, Radarr, Lidarr, Readarr, Mylar3, Prowlarr, qBittorrent, Plex, CF DNS read)
    ├── network.py          ← 7 tools (UniFi)
    ├── homeauto.py         ← 16 tools (DIRIGERA, Apple, Homebridge, Scrypted)
    └── control.py          ← 29 mutating tools across all domains
```

The new `server.py`:

```python
"""Homelab MCP monolith server — orchestrator that registers all
133 tools from per-domain modules onto a single FastMCP instance."""

from homelab_mcp.app import create_mcp
from homelab_mcp.tools import platform, media, network, homeauto, control

mcp = create_mcp()

platform.register(mcp)
media.register(mcp)
network.register(mcp)
homeauto.register(mcp)
control.register(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

Each `tools/{domain}.py` follows this pattern:

```python
"""Domain: {domain}."""

from typing import Any  # plus domain-specific imports

from homelab_mcp.audit import audit
from homelab_mcp.clients import ...
from homelab_mcp.policy import check_readonly
from homelab_mcp.settings import ...


def register(mcp: Any) -> None:
    @mcp.tool()
    def tool_one(...):
        """..."""
        ...

    @mcp.tool()
    def tool_two(...):
        ...
```

#### Procedure

1. Create `tools/__init__.py`.
2. For each domain in `[platform, media, network, homeauto, control]`:
   a. Create `tools/{domain}.py`.
   b. Write the `register(mcp)` function shell.
   c. For every tool whose `server` field in `tool-inventory.json`
      matches `homelab-mcp-{domain}`, COPY its `@mcp.tool()` block
      from the current `server.py` into the new module's `register()`
      body, indented one level. Tool body is byte-identical to source.
   d. Add domain-specific imports (clients, settings helpers).
3. Replace `server.py` body with the new ~60-line orchestrator.
4. Run `python tools/verify_lift.py` — must report 133 tools, no dupes.
5. Run `python tools/validate_inventory.py --source-repo .` — must
   report `OK: 133/29/104`.
6. Run `pytest mcp/tests -q` — must be 145 passed.
7. `docker build -t homelab-mcp:phase-1.0-test mcp/` then
   `docker run` and `curl localhost:8080/openapi.json | jq
   '.paths | length'` — must equal 133.

#### Risks

- **R1: tool name drift on copy/paste.** Mitigated by `verify_lift.py`
  (G-5) which AST-scans the union of all `tools/*.py` and asserts set
  equality with `tool-inventory.json`.
- **R2: import cycle.** `tools/{domain}.py` imports from
  `homelab_mcp.{audit,clients,policy,settings}` only; no tool module
  imports another tool module. Linear DAG.
- **R3: test still expects `homelab_mcp.server` to define the tools
  directly.** The test suite (`test_architecture_refactor_contract.py`)
  uses AST + decorator detection on `server.py`. Will need to be
  updated to scan `tools/*.py` instead. This update is part of this
  SDD's scope, not a follow-up.

#### Out of scope

- Per-domain image (Phase 1.1+).
- Per-domain console script (Phase 1.1+).
- New CI workflows (Phase 1.1+).
- Bundle multiplexer (Phase 1.6).
- Cluster cutover (Phase 1.7).

---

## Phase 1.1 — `homelab-mcp-network` (sketch)

After Phase 1.0 ships, the smallest domain (7 tools) becomes a
30-line `packages/homelab-mcp-network/main.py` that imports
`homelab_mcp.tools.network.register` against its own
`create_mcp()` instance, ships its own `Dockerfile` (FROM same base),
and gets its own `release-network.yml` workflow following the
existing release-monolith pattern.

Trust boundary: read-only, no `policy.WRITE_TOOLS`, no
`HOMELAB_MCP_READONLY` flag (read-only by construction).

Inventory: `verify_lift.py --domain network` (new gate flag) asserts
the 7 tool names match `tool-inventory.json`'s `server == homelab-mcp-network`.

Registry: `ghcr.io/dragoshont/homelab-mcp-network:v0.1.0`,
`hserver/homelab-mcp-network:v0.1.0`.

This pattern is then mechanically applied to phases 1.2-1.5.

## Phase 1.6 — `homelab-mcp-bundle` (sketch)

A YAML-config-driven multiplexer:

```yaml
# /etc/homelab-mcp/bundle.yaml
servers:
  - platform
  - media
  - network
  - homeauto
  # control: opt-in via env var or separate flag
control:
  enable: false
  bearer_token_env: HOMELAB_MCP_CONTROL_TOKEN
```

Runs each enabled `tools/{domain}.register(mcp)` against a single
FastMCP instance. **This is the drop-in replacement for today's
monolith**, with the option to disable `control` to get a strictly
read-only deployment.

## Phase 1.7 — cluster cutover (sketch)

Replace
`image: ghcr.io/dragoshont/homelab-mcp:v1.x.y` with
`image: ghcr.io/dragoshont/homelab-mcp-bundle:v0.x.y`.
Same env vars (Phase 0.4 contract). Same K8s service, same OpenWebUI
`TOOL_SERVER_CONNECTIONS` config. Smoke from open-webui pod must
return 133 paths.

After 24h soak: optionally split the cluster into domain-scoped
deployments (one Pod per domain). That's Phase 2 (not 1).
