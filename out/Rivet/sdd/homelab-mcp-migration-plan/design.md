# Design — homelab-mcp-migration-plan

**Workflow:** `homelab-mcp-migration-plan` (#0)
**Repo:** `dragoshont/homelab_mcp` (public)
**Status:** Step 3 (Spec/PRD — design half)
**Reads with:** `spec.md`, `contract.md`

---

## 1. Architecture overview

```
                ┌───────────────────────┐
                │     OpenWebUI (k8s)   │
                └───────────┬───────────┘
                            │ MCP openapi.json (multi-endpoint)
        ┌───────────────────┼─────────────────────────────┐
        │           │       │              │              │
        ▼           ▼       ▼              ▼              ▼
   ┌─────────┐ ┌──────┐ ┌──────┐    ┌────────────┐  ┌─────────────┐
   │platform │ │media │ │network│    │ homeauto   │  │ control     │
   │  (RO 51)│ │(RO 30)│ │(RO 7)│    │ (RO 16)    │  │ (W 29, opt) │
   └─────────┘ └──────┘ └──────┘    └────────────┘  └─────────────┘
        │
        └─── kube/host/flux-RO/etc. live here

   ┌──────────────────────────────────────────────────────────────┐
   │ homelab-mcp-proxy:1.1.0  (the existing monolith, 133 tools) │
   │   stays running for the full migration as the fallback      │
   └──────────────────────────────────────────────────────────────┘
```

OpenWebUI is connected to the monolith today. During migration it gains
*additional* MCP endpoints, one per split server, while the monolith URL
stays registered. The monolith URL is removed only after a phase completes
and the new endpoint is verified.

## 2. Target split

5 servers total: 4 readonly + 1 opt-in control. The split groups by **trust
boundary**, not by source-code prefix; some prefixes contribute tools to two
servers (their RO half and their write half).

### 2.1 Per-server table

| # | Server | Role | Tools | Write tools | Read-only tools | Source prefixes |
|---|--------|------|-------|-------------|-----------------|-----------------|
| 1 | `homelab-mcp-platform` | readonly | 51 | 0 | 51 | kube(18 RO), host(15), ansible(2), backup(2), image(3), gitops(3), flux(2 RO), audit(1), cert(1), dns(1), homelab(1), ingress(1), netdata(1) |
| 2 | `homelab-mcp-media` | readonly | 30 | 0 | 30 | sonarr(5 RO), radarr(5 RO), lidarr(2 RO), readarr(2 RO), mylar3(3 RO), prowlarr(3 RO), qbt(2 RO), plex(3 RO), media(3), cf(2) |
| 3 | `homelab-mcp-network` | readonly | 7 | 0 | 7 | unifi(7 RO) |
| 4 | `homelab-mcp-homeauto` | readonly | 16 | 0 | 16 | dirigera(7 RO), homebridge(4), scrypted(1), apple(4 RO) |
| 5 | `homelab-mcp-control` | control (opt-in) | 29 | 29 | 0 | kube(2 W), flux(3 W), apple(5 W), dirigera(4 W), unifi(4 W), plex(2 W), prowlarr(2 W), qbt(2 W), sonarr(1 W), radarr(1 W), lidarr(1 W), readarr(1 W), mylar3(1 W) |
| **Σ** | | | **133** | **29** | **104** | all 28 source prefixes covered |

Sum check: 51 + 30 + 7 + 16 + 29 = **133** ✓
RO sum: 51 + 30 + 7 + 16 = **104** ✓
Writes: **29** ✓

### 2.2 Why network is its own server (only 7 tools)

`unifi_*` represents the *network control plane* trust boundary (block/unblock
clients, reconnect, set wlan, list devices). Even the read-only half (list
clients, list devices) is sensitive: it's an inventory of every device on the
home LAN. Co-locating it with cluster ops (platform) would mix two orthogonal
"who can see the LAN" and "who can see the cluster" exposures. Keeping it
separate is cheap (one small image) and gives a clean answer to "what does
the AI see when I connect this server."

### 2.3 Why control is opt-in and last

The 29 write-tools span every domain. A single bug in any one of them is the
worst-case blast radius (e.g., `kube_restart` on the wrong namespace,
`unifi_block` on the operator's own laptop). Building the readonly servers
first lets us prove the split mechanism (packaging, transport, registration,
naming) on safe surfaces before the control server ships.

## 3. Module/package layout (target, post-migration)

```
homelab_mcp/                   ← this repo
├── README.md
├── docs/migration/            ← introduced by this PR
│   ├── migration-plan.md
│   ├── tool-inventory.json
│   └── verification/          ← per-tool smoke evidence (added in later phases)
├── packages/
│   ├── homelab-mcp-core/      ← shared FastMCP, audit, policy, settings
│   ├── homelab-mcp-platform/
│   ├── homelab-mcp-media/
│   ├── homelab-mcp-network/
│   ├── homelab-mcp-homeauto/
│   ├── homelab-mcp-control/
│   └── homelab-mcp-bundle/    ← config-driven multi-server runner (no new tools)
├── containers/                ← per-server Dockerfile + bundle Dockerfile
├── deploy/                    ← reference K8s manifests (no homelab specifics)
└── .github/workflows/         ← per-server + bundle CI (matrix build → GHCR)
```

This PR only creates `docs/migration/`. The `packages/`, `containers/`,
`deploy/` trees are introduced by the per-phase SDDs that follow.

## 4. Console scripts and entry points

Each server is a separate Python package that depends on `homelab-mcp-core`
and exposes one console script. Single Python distribution per server keeps
PyPI/installation simple and lets contributors install only what they need.

| Server | Console script | Image tag (planned) |
|--------|----------------|----------------------|
| platform | `homelab-mcp-platform` | `ghcr.io/dragoshont/homelab-mcp-platform:<sha>` + `dragoshont/homelab-mcp-platform:<semver>` (releases) |
| media | `homelab-mcp-media` | `ghcr.io/dragoshont/homelab-mcp-media:<sha>` + `dragoshont/homelab-mcp-media:<semver>` (releases) |
| network | `homelab-mcp-network` | `ghcr.io/dragoshont/homelab-mcp-network:<sha>` + `dragoshont/homelab-mcp-network:<semver>` (releases) |
| homeauto | `homelab-mcp-homeauto` | `ghcr.io/dragoshont/homelab-mcp-homeauto:<sha>` + `dragoshont/homelab-mcp-homeauto:<semver>` (releases) |
| control | `homelab-mcp-control` | `ghcr.io/dragoshont/homelab-mcp-control:<sha>` + `dragoshont/homelab-mcp-control:<semver>` (releases) |
| **bundle (all-in-one)** | `homelab-mcp-bundle --config <path>` | `ghcr.io/dragoshont/homelab-mcp-bundle:<sha>` + `dragoshont/homelab-mcp-bundle:<semver>` (releases) |
| (existing) monolith | `homelab-mcp` (unchanged) | `homelab-mcp-proxy:1.1.0` |

The monolith's `homelab-mcp` console script and its image stay in the source
repo and remain operational throughout.

### 4.1 The bundle image (config-driven multi-server)

`homelab-mcp-bundle` is a deployment convenience, **not** a new architectural
layer:

- It depends on `homelab-mcp-core` and on each of the 5 server packages.
- It contains **zero tool implementations of its own**. The same 133 tools
  live in the same 5 server packages; the bundle just imports and exposes
  them.
- Its only job is to read a config file and start a subset of the 5 servers
  in the same process tree (each on its own port / mount path) so a single
  container can replace the existing monolith for users who don't want to
  run 5 Pods.

This preserves the trust-boundary value of the split (each server is still
a distinct MCP endpoint with its own tool set, OpenWebUI registers each
separately) while keeping operational complexity comparable to today's
monolith for small homelab/dev users.

**Config schema (`bundle.yaml`)** — minimal, declarative, validated at startup:

```yaml
# Each entry maps to one of the 5 server packages.
# Disabled servers are NOT loaded; their tools are not in the registered set.
servers:
  platform:
    enabled: true
    mount: /mcp/platform     # path under the bundle's HTTP root
    port: null               # null = share bundle's port; int = bind dedicated port
  media:
    enabled: true
    mount: /mcp/media
  network:
    enabled: false           # operator opted out
  homeauto:
    enabled: true
    mount: /mcp/homeauto
  control:
    enabled: false           # control NEVER auto-enabled; operator must set true
    mount: /mcp/control
    auth:
      bearer_token_env: HOMELAB_MCP_CONTROL_TOKEN  # required when enabled=true
bundle:
  bind: 0.0.0.0
  port: 8080
  audit_sink:                # resolves Q5 for the bundle case
    type: file
    path: /var/log/homelab-mcp/bundle-audit.log
```

**Bundle-specific gates (extend G1–G5 in §6 for the bundle image):**

| Bundle gate | Check |
|-------------|-------|
| **B1** | Bundle with `enabled: true` for all 5 servers exposes exactly 133 tools (set-equality vs `tool-inventory.json`) — proves the bundle is not silently filtering tools. |
| **B2** | Bundle with `control.enabled: false` exposes exactly 104 tools and zero entries from `WRITE_TOOLS` — proves opt-in works. |
| **B3** | Bundle with `control.enabled: true` and `bearer_token_env` unset **fails to start** with a clear error — proves auth is enforced for the in-process control server, same as the dedicated control server. |
| **B4** | Two bundles started with disjoint configs (e.g., one with `media+platform`, one with `network+homeauto+control`) together expose the same tool set as one all-on bundle — proves the split is composable. |

**When to use which deployment shape:**

| Shape | Best for | Cost |
|-------|----------|------|
| 5 separate Pods (per-server images) | Production multi-tenant; want per-server resource limits, separate NetworkPolicies, independent rollouts | 5× scheduling overhead |
| 1 bundle Pod (bundle image, all servers enabled) | Solo homelab; matches today's monolith UX; one container to manage | Loses per-server NetworkPolicy granularity within the Pod |
| 1 bundle Pod (subset enabled) | Edge / minimal install (e.g., "only media+platform") | — |
| Mixed (bundle for RO + dedicated control Pod) | Recommended default for homelab: bundle the 4 RO servers, run control as its own Pod with stricter NetworkPolicy | Two Pods, but cleanly separates control trust boundary |

The bundle is **delivered after Phase 4** (or in parallel with Phase 4): it
cannot exist before all 5 server packages exist. A new "Phase 4.5: bundle"
SDD packages and ships it.

### 4.2 CI / image build & release strategy (resolves Q7, Q8)

Two workflows live at `.github/workflows/`:

**A. `build-images.yml` — every push to `main`:**

```yaml
# pseudocode shape - full file lands in Phase 1
on:
  push:
    branches: [main]
    paths:
      - 'packages/homelab-mcp-${{ matrix.server }}/**'
      - 'packages/homelab-mcp-core/**'   # any core change rebuilds all
      - 'containers/Dockerfile.${{ matrix.server }}'
strategy:
  matrix:
    server: [platform, media, network, homeauto, control, bundle]
permissions:
  contents: read
  packages: write     # GHCR push only; Docker Hub NOT touched on every push
```

Pushes to `ghcr.io/dragoshont/homelab-mcp-{server}:{git-sha}` and
`:main`. Path filters skip rebuilds for unrelated changes. **Docker Hub
is NOT pushed from this workflow** — every-commit publishing to a public
registry creates noise and increases the secret-leak blast radius.

**B. `release-images.yml` — GitHub release / tag `v*.*.*`:**

```yaml
on:
  release:
    types: [published]   # only when an operator clicks "publish release"
  workflow_dispatch:     # manual fallback for re-publishing a release
strategy:
  matrix:
    server: [platform, media, network, homeauto, control, bundle]
permissions:
  contents: read
  packages: write
```

This workflow:
1. Reads the release tag (e.g. `v0.4.1`), validates semver.
2. Pulls the GHCR image tagged with the commit SHA the release points to
   (the image was already built by `build-images.yml`); does NOT rebuild.
3. Re-tags it as `:v0.4.1`, `:0.4` (minor track), `:latest` and pushes to
   both GHCR and Docker Hub.
4. Logs in to Docker Hub via `docker/login-action` using
   `secrets.DOCKERHUB_USERNAME` / `secrets.DOCKERHUB_TOKEN`.
5. Updates the Docker Hub repo's short description / README from
   `containers/{server}.dockerhub.md` (one file per image).
6. Generates and attaches the SBOM and a cosign signature to both registries.

**Two-registry rationale:**

| Registry | Role | Pushed when |
|----------|------|-------------|
| `ghcr.io/dragoshont/homelab-mcp-{server}` | CI artifact + canonical source for releases | Every push to `main` |
| `dragoshont/homelab-mcp-{server}` (Docker Hub) | OSS discoverability, default pull URL for community users | Only on `release: published` |

Users who follow the project pull from Docker Hub by default; users who
want the latest unreleased build pull from GHCR (`:main` or `:{sha}`).
The Docker Hub side is intentionally slower-moving and tagged.

**Required repo secrets (added by operator before the workflow can
succeed; absence does NOT break the GHCR-only path):**

| Secret | Source | Scope |
|--------|--------|-------|
| `DOCKERHUB_USERNAME` | Operator's Docker Hub login | Public repo write |
| `DOCKERHUB_TOKEN` | Docker Hub Account Settings → Personal access tokens → Public Repo Read & Write, 1-year expiry, name `homelab_mcp_ghactions` | Per-token, rotatable |

The `release-images.yml` workflow's first step is `if:
${{ secrets.DOCKERHUB_TOKEN != '' }}` so a release without the secrets
falls back to GHCR-only publishing with a warning rather than failing.

**Security-relevant:** the workflow uses
`permissions: { packages: write, contents: read }` only in the publish
step; build/test runs with `read-all`. The composite action pins all base
images by digest, not tag, so a base-image tag re-point cannot silently
change the published image content. Phase 1 SDD locks the digest set.

**Composite action** at `.github/actions/build-mcp-image/` is shared by
all matrix legs in both workflows to keep the workflow files small and
the build/test/scan logic in one place.

## 5. Transport and security

| Concern | Readonly servers | Control server |
|---------|------------------|----------------|
| Transport | Streamable HTTP, in-cluster | Streamable HTTP, in-cluster, **distinct port and Service** |
| K8s Service | per-server `ClusterIP` | per-server `ClusterIP` with separate name |
| NetworkPolicy | ingress from OpenWebUI pods only (label `app.kubernetes.io/name=open-webui`) | ingress from OpenWebUI **plus** a second label gate (`mcp.homelab/control-allowed=true`) the operator must opt the OpenWebUI pod into |
| Origin validation | Required (per MCP spec) | Required + audit log every call regardless of success |
| Auth | shared cluster-internal trust | bearer token from a K8s Secret (verified at app layer). Rotation policy is **declared in the Phase 4 SDD**, not here; static token from a Secret is acceptable for v1 with a documented rotation runbook. The unsupported claim "rotates" has been removed pending Phase 4. |
| Default OpenWebUI wiring | all readonly endpoints registered by default | NOT registered until operator explicitly opts in |
| Image policy | `imagePullPolicy: IfNotPresent` (after registry publish) | `imagePullPolicy: IfNotPresent` |
| Pod security | `runAsNonRoot: true`, read-only root FS | same + `allowPrivilegeEscalation: false`, drop ALL caps |

## 6. Per-server acceptance gates (G1..G5)

Each split server must pass all of these before its phase ends. Failing any
gate blocks the phase; the monolith continues to serve the affected tools.

| Gate | Name | Check |
|------|------|-------|
| **G1** | Inventory parity | The split server's registered tool name set equals exactly its assigned subset from `tool-inventory.json`. Asserted by a test that imports the server's FastMCP app and compares to JSON. |
| **G2** | Readonly enforcement | For RO servers: importing the server fails (or its tests fail) if any tool name from `WRITE_TOOLS` is registered. For control: importing fails if any tool from a RO subset is registered. |
| **G3** | Smoke | From a pod labeled `app.kubernetes.io/name=open-webui`, `curl /docs` and `curl /openapi.json` return 200, and `openapi.json` lists exactly the expected tool count. |
| **G4** | Side-by-side parity | For RO servers: an automated harness picks 3 representative read-only tools from the server's subset, calls them on both monolith and split, asserts the two results have the same JSON shape (keys equal, types equal). **For the control server, G4 is "request-shape parity" (not live mutation):** the harness captures the rendered downstream request payload that each write-tool would issue (e.g., kube `ApplyConfiguration` body, unifi REST body, dirigera command DSL) from both monolith and split and asserts byte-for-byte equality of the request, never firing the mutation. Idempotency/non-determinism of the live action is therefore irrelevant. |
| **G5** | Network isolation | RO servers' NetworkPolicy denies ingress from non-OpenWebUI pods (verified by a curl from a non-matching pod returning connection refused/timeout). Control server's policy additionally requires the second label gate. |

## 7. Phased rollout

| Phase | Server | Why this order | Rollback |
|-------|--------|----------------|----------|
| 0 (this PR) | none | Plan + inventory only; no runtime change | revert PR |
| 1 | `homelab-mcp-platform` | Largest RO surface, lowest blast radius; proves the split mechanism with no writes; covers our most-used tools (kube, host, image) | un-register OpenWebUI endpoint; monolith unchanged |
| 2 | `homelab-mcp-media` | Second largest, fully RO, isolated from infra | un-register endpoint |
| 3 | `homelab-mcp-network` and `homelab-mcp-homeauto` (parallel) | Small, independent, can ship together | un-register either independently |
| 4 | `homelab-mcp-control` | Last; mutating; opt-in connection from OpenWebUI | leave un-registered; monolith continues to serve writes |
| 5 | Monolith decommission | Only after Phases 1–4 are gate-green and OpenWebUI is wired exclusively to splits | re-register the monolith URL — image still in containerd cache |

Each numbered phase is its own future SDD in this repo; this SDD does not
execute them.

### 7.1 Enforceable phase-status tracker (AS-3 mitigation)

Decommission of the monolith (Phase 5) is gated by an asserted artifact, not
by a prose claim. Each phase SDD appends an entry to **`docs/migration/phase-status.json`**
(append-only) at the moment its acceptance gate passes:

```json
{
  "phase": 1,
  "server": "homelab-mcp-platform",
  "gates_passed": ["G1", "G2", "G3", "G4", "G5"],
  "passed_utc": "2026-06-01T12:00:00Z",
  "evidence_path": "docs/migration/verification/phase-1/"
}
```

**Phase 5 entry-criterion script** (run by Phase 5 SDD):

1. `phases = read('docs/migration/phase-status.json')`
2. Assert `len(phases) == 5` AND every gate in `{G1..G5}` passed for every server.
3. Assert the set of `server` values equals the 5 split server names exactly.
4. **OpenWebUI grep gate:** `grep -r homelab-mcp-proxy.default.svc.cluster.local apps/platform/openwebui/` MUST return zero matches in the source repo at the pinned commit (or the latest re-pin) before Phase 5 advances.

If any of (2), (3), (4) fails, the Phase 5 SDD is blocked. "All gates green" is therefore an asserted, machine-checked condition — not an operator claim.

### 7.2 Phase 1 first-deliverable: OpenWebUI overlap test (AS-6 mitigation)

The earliest task of the Phase 1 SDD is to **empirically verify OpenWebUI's
tool-name overlap behavior** when two MCP endpoints expose the same tool name.
Three outcomes possible:

1. Deterministic dedup (one wins by registration order or alphabetical) — cutover order can be "add split, then remove monolith".
2. Non-deterministic dedup — cutover order MUST be "remove monolith's coverage of the tool, then add split".
3. Both registered (model sees duplicates) — cutover order MUST be "remove monolith's coverage first".

The result is recorded in `docs/migration/openwebui-overlap-result.md` and
the cutover checklist in this design doc is updated accordingly before any
phase ships.

### 7.3 Phase 3 parallel ship — locked core (AS-11 mitigation)

Network and homeauto ship in parallel in Phase 3, both depending on
`homelab-mcp-core`. To prevent a breaking core change from affecting one
but not the other:

- Phase 3 SDD pins `homelab-mcp-core` to a single locked version in both
  `packages/homelab-mcp-network/pyproject.toml` and
  `packages/homelab-mcp-homeauto/pyproject.toml`.
- Build order is sequential: build network first, lock core version,
  build homeauto against the same lock.
- Both servers' images are tagged with the locked core version in their
  metadata so a runtime mismatch is detectable.

## 8. Risk mitigations

| Risk (from spec §8) | Mitigation |
|---|---|
| RK-1 inventory drift | Snapshot pinned to a specific source-repo commit; SDD step at start of each phase re-snapshots and aborts if delta exists. |
| RK-2 hidden mutation | Tools currently in `WRITE_TOOLS` are the source of truth. Any tool we suspect is mis-classified gets added to `WRITE_TOOLS` in the source repo first (separate PR), not in this plan. |
| RK-3 naming collision in OpenWebUI | All tool names are kept verbatim. With multi-endpoint MCP, OpenWebUI distinguishes by server URL; collisions across servers are impossible because the inventory enforces no tool on two servers. **Collision during overlap (monolith + split both registered for the same tool) is bounded by §7.2's overlap test, which decides the cutover order**: if OpenWebUI's behavior is dedup-deterministic, cutover is "add split, then remove monolith"; if non-deterministic or both-registered, cutover is "remove monolith's coverage of the tool first, then add split". This mitigation does not assume any particular outcome — it makes the experimental result the gating input for the per-tool cutover checklist. |
| RK-4 control auth weaker than monolith | Control server uses NetworkPolicy + a second label gate AND an app-layer bearer token. Strictly more constraints than the monolith. |
| RK-5 inventory churn during migration | Source repo is in maintenance mode for new tools during phases 1–4; new tools land in the *split server* that owns the prefix, not the monolith. Documented in `migration-plan.md`. |
| RK-6 helper drift | `homelab-mcp-core` package is the only home for shared helpers (FastMCP app factory, audit logger, policy enforcement, settings). Servers depend on it; ad-hoc copies in servers are flagged by the per-phase SDD's adversarial review. |
| RK-7 public repo leak | This PR carries no homelab-specific values. Phase SDDs include a grep gate over hostnames/IPs/known secret patterns before push. **Limitation:** grep against a known-pattern list misses unknown patterns (custom hostnames, encoded secrets). Phase 0 (this PR) ships only docs and JSON, so the residual risk is low. **Phase 1 SDD upgrades the scanner to a tool that does not rely on a static pattern list (e.g., `gitleaks` or equivalent)** before any deployment manifests are committed. |

## 9. Tool-inventory.json schema (delivered in Step 5)

```json
{
  "source_commit": "0727116cc8217994bbb1a8d083bc95140671a580",
  "captured_utc": "2026-05-01T00:00:00Z",
  "totals": { "tools": 133, "writes": 29, "readonly": 104 },
  "servers": {
    "homelab-mcp-platform": { "role": "readonly", "tools": 51 },
    "homelab-mcp-media":    { "role": "readonly", "tools": 30 },
    "homelab-mcp-network":  { "role": "readonly", "tools": 7 },
    "homelab-mcp-homeauto": { "role": "readonly", "tools": 16 },
    "homelab-mcp-control":  { "role": "control",  "tools": 29 }
  },
  "tools": [
    { "name": "kube_pods", "server": "homelab-mcp-platform", "mutating": false },
    { "name": "kube_restart", "server": "homelab-mcp-control", "mutating": true }
    /* ... 131 more ... */
  ]
}
```

Generation rule (deterministic, scriptable):
1. AST-scan source repo `mcp/src/homelab_mcp/server.py` at the pinned commit, recognising decorators of the form `@mcp.tool(...)` (`ast.Call` with `ast.Attribute.attr == 'tool'`) and `@mcp.tool` (bare `ast.Attribute.attr == 'tool'`). **Strict mode (AS-14 mitigation):** if any top-level `FunctionDef` or `AsyncFunctionDef` in `server.py` carries a decorator with an unrecognised name (e.g., `@register_tool`, `@mcp.command`), the scan FAILS LOUDLY rather than silently skipping. Tools cannot disappear from the inventory because a future decorator form was not anticipated.
2. Read `WRITE_TOOLS` from `mcp/src/homelab_mcp/policy.py`.
3. Map prefix → server using §2.1 table.
4. For each tool, `mutating = (name in WRITE_TOOLS)`; if mutating, server = `homelab-mcp-control`, else server = prefix→server map.
5. **Set-equality check:** `set(scanned_tools) == set(inventory_tools)`; the intersection of any two server tool sets MUST be empty.
6. Sum-check: 133 / 29 / 104.

### 9.1 Phase 0 inventory validation step (AS-9 mitigation)

Because Phase 0 (this PR) makes no Python code changes, `rivet build` /
`pytest` are trivially green and prove nothing about the plan's correctness.
Step 7 of this SDD therefore runs an explicit content-validation script
**`tools/validate_inventory.py`** (delivered in Step 5) which:

1. Loads `docs/migration/tool-inventory.json` and asserts its schema.
2. AST-scans the source repo at the pinned commit using the strict rule above.
3. Asserts set-equality between scanned tools and inventory tools.
4. Asserts every inventory tool's `mutating` flag matches `WRITE_TOOLS` membership.
5. Asserts every server's tool list is disjoint from every other server's.
6. Exits non-zero with the offending diff on any failure.

The existing `rivet build` invocation is allowed to no-op for Phase 0;
`validate_inventory.py` is the real Step-7 gate.

### 9.2 Hidden-mutation candidate scan (AS-1 mitigation, deferred)

This plan trusts `WRITE_TOOLS` as the single source of mutation
classification. A tool that mutates state but is missing from `WRITE_TOOLS`
would be silently placed on a readonly server and the gate would still
green. Detecting that is **out of scope for Phase 0** but **mandatory for
Phase 1**: the Phase 1 SDD adds a heuristic AST scan over each tool body
looking for `subprocess.run`, mutating HTTP verbs (`POST`, `PUT`, `PATCH`,
`DELETE`) without a known read-only allowlist, and `kubectl apply|delete`
strings, and surfaces candidates. Tools that fail the scan must be added
to `WRITE_TOOLS` in the source repo (separate PR) before being placed by
this plan.

## 10. Out of scope (re-stated for design clarity)

- New MCP gateway/aggregator (could come later as a separate SDD).
- Cross-cluster federation.
- Replacing `mcpo` with native Streamable HTTP support inside FastMCP.
- Re-implementing the audit logger or policy framework — kept as is, lifted into `homelab-mcp-core` unchanged.

## 11. Open questions deferred to phase SDDs

- **Q1:** Where do `kube_image_can_pull` (currently flagged write because it
  pulls test images) belong long-term? Phase 1 SDD revisits.
- **Q2:** Should `cf_*` (cross-seed/cross-fork) live in media or platform?
  Currently in media; Phase 2 SDD reconfirms.
- **Q3:** Where does `audit_*` (1 tool — query audit log) belong? Currently in
  platform; Phase 1 SDD reconfirms — could move to a future "meta" server.
- **Q4 (AS-1):** Hidden-mutation detection. Phase 1 SDD adds a heuristic
  AST scanner that flags tools whose bodies look mutating but are absent
  from `WRITE_TOOLS`. Result either confirms current classification or
  produces a list of source-repo PRs to add tools to `WRITE_TOOLS` before
  the placement is finalised.
- **Q5 (AS-7):** Audit sink topology when 5 servers each run their own
  audit logger. Choices: (a) per-server audit file with a documented
  aggregator (rsyslog → central path), (b) shared sink (syslog/journald),
  (c) per-Pod file with hostname suffix and offline aggregation. The
  monolith's current single-file write is unsuitable for multiple Pods
  and is **explicitly rejected**. Decided in Phase 1 before any second
  server ships.
- **Q6 (AS-13):** Re-evaluate the network server after Phase 3. If the
  7-tool surface proves operationally noisier than valuable, consider
  folding it into platform with an explicit `unifi.*` tool naming prefix
  preserved for trust-boundary clarity. Decision recorded in the Phase 3
  retrospective.
- **Q7 (CI / image build strategy — resolved in §4.2):** Two-workflow split:
  `build-images.yml` (matrix; every push to main; GHCR only) and
  `release-images.yml` (matrix; on GitHub release; promotes a GHCR-tagged
  image to Docker Hub + GHCR with semver tags). Phase 1 SDD lands both
  workflows + the shared composite action and pins the base-image digest
  set.
- **Q8 (Docker Hub publishing — resolved in §4.2):** Yes, on releases only,
  via the `release-images.yml` workflow. Docker Hub is the
  community-facing default (tagged, slower-moving); GHCR is the canonical
  CI artifact (every-commit). Both registries get the same content for a
  given release tag (re-tag, not rebuild). Required secrets
  (`DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`) are operator-provided; missing
  secrets gracefully degrade the release workflow to GHCR-only publishing.


## 12. Test Plan

Phase 0 (this PR) has no Python code changes; therefore "tests" for this SDD
are content-validation scripts, not pytest. The Step-7 build invocation
runs `tools/validate_inventory.py` (see §9.1) which constitutes the executable
test plan for Phase 0.

### 12.1 MUST PASS test cases

| ID | Test | Asserts |
|----|------|---------|
| T1 | `validate_inventory.py --schema` | `tool-inventory.json` validates against the schema in §9 (required keys, types). |
| T2 | `validate_inventory.py --counts` | `len(tools) == 133`, `len(mutating) == 29`, `len(readonly) == 104`. |
| T3 | `validate_inventory.py --set-equality` | `set(scanned_tools_at_pinned_commit) == set(inventory_tools)`. |
| T4 | `validate_inventory.py --disjoint` | Pairwise intersection of every server's tool set is empty. |
| T5 | `validate_inventory.py --write-isolation` | Every tool with `mutating: true` has `server == "homelab-mcp-control"`. No tool with `mutating: false` is on the control server. |
| T6 | `validate_inventory.py --strict-decorators` | AST scan over `server.py` finds zero top-level functions with unrecognised decorators (AS-14). |
| T7 | `validate_inventory.py --write-tools-match` | The set of tools with `mutating: true` equals the set in `policy.py:WRITE_TOOLS` exactly. |

### 12.2 MUST FAIL test cases (RC-4 — gate must prove "block")

| ID | Test | Asserts the gate REJECTS |
|----|------|---------------------------|
| T8 | Inject duplicate tool name into inventory | T3/T4 reject with non-zero exit and a diff. |
| T9 | Move one write-tool to a readonly server in inventory | T5 rejects. |
| T10 | Drop one tool from inventory | T2 and T3 reject; sum-only check would have passed (proves we use set-equality, not sum). |
| T11 | Add an unknown tool name to inventory | T3 rejects. |
| T12 | Mark a known write-tool as `mutating: false` | T7 rejects. |

### 12.3 Out-of-scope tests

- pytest over `mcp/tests/` in the source repo. That suite passes (101 tests
  in last verified run) but is not a test of this plan.
- Live HTTP smoke against any split server. No split server exists yet.
- OpenWebUI overlap test. Deferred to Phase 1 (§7.2).
- Side-by-side parity G4. Deferred to per-phase SDDs.

## 13. File Inventory

Files this SDD adds, modifies, or pins as inputs.

### 13.1 Files added by this PR (Phase 0)

| Path | Type | Purpose |
|------|------|---------|
| `out/Rivet/sdd/homelab-mcp-migration-plan/contract.md` | SDD artifact | Verify contract (MUST PASS / MUST FAIL / Integration Points). |
| `out/Rivet/sdd/homelab-mcp-migration-plan/spec.md` | SDD artifact | Spec/PRD. |
| `out/Rivet/sdd/homelab-mcp-migration-plan/design.md` | SDD artifact | This document. |
| `out/Rivet/sdd/homelab-mcp-migration-plan/as-findings.json` | SDD artifact | Adversarial spec findings (AS-1..14). |
| `out/Rivet/sdd/homelab-mcp-migration-plan/context.json` | SDD artifact | Step-1 context preflight (auto-generated). |
| `out/Rivet/sdd/homelab-mcp-migration-plan/state.json` | SDD artifact | CLI state (CLI-managed; do not hand-edit). |
| `docs/migration/migration-plan.md` | Public doc | Public-facing migration plan; mirrors §2.1 split table. |
| `docs/migration/tool-inventory.json` | Data | 133 tools by name, server, mutating flag (§9 schema). |
| `docs/migration/phase-status.json` | Data (append-only seed) | Initialised as `[]`; phase SDDs append entries (§7.1). |
| `docs/migration/inventory-history.json` | Data (append-only seed) | Initialised as `[]`; re-pin entries appended (spec C2). |
| `tools/validate_inventory.py` | Script | Phase-0 Step-7 gate (§9.1). |

### 13.2 Files read but not modified

| Path (source repo `C:\src\homelab\`) | Read for |
|--------------------------------------|----------|
| `mcp/src/homelab_mcp/server.py` | AST scan to enumerate the 133 `@mcp.tool` decorators at pinned commit `0727116c...`. |
| `mcp/src/homelab_mcp/policy.py` | Read `WRITE_TOOLS` (29 names). |
| `mcp/src/homelab_mcp/audit.py` | Confirm single-file audit-write behavior (informs Q5 in §11). |
| `mcp/Dockerfile` | Confirm current image build steps (informs §3 module layout). |
| `apps/platform/mcp-proxy/deployment.yaml` | Confirm `homelab-mcp-proxy:1.1.0`, `imagePullPolicy: Never` (informs §1 fallback claim and §5 image policy). |
| `mcp/tests/` | Confirm 101 passing baseline (informs Phase 0 build no-op rationale). |

### 13.3 Files explicitly NOT touched

- Any file under `C:\src\homelab\` (source repo) — enforced by contract MUST-FAIL #8.
- `README.md` of this repo — left at its initial state for now; phase-1 SDD updates it once at least one split server ships.
- Any `packages/`, `containers/`, `deploy/` directory — these are introduced by phase-1 SDD and onward, not Phase 0.

### 13.4 Files marked CLI-only (per current mode rule)

The following SDD artifacts are managed exclusively by `rivet sdd` commands;
agents and humans MUST NOT hand-edit them:

- `out/Rivet/sdd/homelab-mcp-migration-plan/state.json`
- `out/Rivet/sdd/homelab-mcp-migration-plan/build-result.json` (created at Step 7)
- `out/Rivet/sdd/homelab-mcp-migration-plan/contract-grade.json` (created at Step 8)
- `out/Rivet/sdd/homelab-mcp-migration-plan/verify-result.json` (created at Step 9)
- `out/Rivet/sdd/homelab-mcp-migration-plan/f10-compliance.md` (created at Step 10)
