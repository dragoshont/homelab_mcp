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

### 4.3 Docker MCP Catalog submission (third distribution channel)

In addition to GHCR (every push) and Docker Hub `dragoshont/*` (releases),
each split server + the bundle gets submitted to the **[Docker MCP
Catalog](https://hub.docker.com/mcp)** at [docker/mcp-registry](https://github.com/docker/mcp-registry)
once it is gate-green and has been on Docker Hub for at least one tagged
release. This is the third distribution target and is **manual, one-time
per server** (not part of every release).

**What it gives us:**

- Listing on `hub.docker.com/mcp` and discoverability in Docker Desktop's
  MCP Toolkit UI (alongside official servers like GitHub, Notion,
  Playwright).
- Optional Docker-built variant: if we let Docker build the image (we
  don't pass `--image`), the published artifact in `hub.docker.com/u/mcp`
  carries Docker's cryptographic signatures, provenance, SBOMs, and
  automatic security updates. Otherwise, the catalog entry references our
  own `dragoshont/homelab-mcp-{server}` image and we keep full control of
  the build pipeline.
- 24h propagation to the catalog, MCP Toolkit, and the `mcp` namespace
  after the docker/mcp-registry PR merges.

**Per-server submission asset table** (lands in this repo at
`packages/homelab-mcp-{server}/docker-mcp-registry/`, not in the registry
repo itself):

| File | Purpose |
|------|---------|
| `server.yaml` | Catalog metadata (name, image, category, tags, source repo, secrets, env, parameters) |
| `tools.json` | Static tool list — required for our case because the readonly servers won't list tools without their upstream credentials configured (avoids the `task build --tools` failure documented in CONTRIBUTING.md) |
| `readme.md` | Server description shown on the catalog page |
| `icon.png` (optional) | 200x200 server icon |

**`server.yaml` shape** (sketch for `homelab-mcp-platform`):

```yaml
name: homelab-mcp-platform
image: dragoshont/homelab-mcp-platform   # we provide the image; Docker won't rebuild
type: server
meta:
  category: monitoring                   # closest catalog category
  tags:
    - kubernetes
    - homelab
    - readonly
    - inventory
about:
  title: Homelab MCP — Platform (read-only)
  description: |
    Read-only inventory and observation tools for a homelab Kubernetes
    cluster, host filesystem, FluxCD GitOps state, and image registry.
    51 tools, all read-only. No mutations.
  icon: https://raw.githubusercontent.com/dragoshont/homelab_mcp/main/assets/icons/platform.png
source:
  project: https://github.com/dragoshont/homelab_mcp
  commit: <release-commit-sha>
config:
  description: Configure access to the operator's homelab cluster
  secrets:
    - name: homelab-mcp-platform.kubeconfig
      env: KUBECONFIG
      example: /path/to/kubeconfig
  env:
    - name: HOMELAB_MCP_AUDIT_LOG
      example: /var/log/homelab-mcp-platform.log
      value: '{{homelab-mcp-platform.audit_log}}'
  parameters:
    type: object
    properties:
      audit_log:
        type: string
        description: Path inside the container where audit log is written
    required: []
```

**Per-server `tools.json`** is generated by Phase 1+ SDDs from
`docs/migration/tool-inventory.json` filtered to the server's tool set
plus a description column harvested from the source repo's tool
docstrings. This is the same list `task build --tools` would otherwise
introspect.

**Submission process** (manual, performed by operator per server, after
that server's first Docker Hub release):

1. Fork [`docker/mcp-registry`](https://github.com/docker/mcp-registry).
2. Run `task wizard` (or `task create -- --category monitoring --image dragoshont/homelab-mcp-platform https://github.com/dragoshont/homelab_mcp`) to scaffold `servers/homelab-mcp-platform/server.yaml`.
3. Replace the scaffolded files with our maintained
   `packages/homelab-mcp-{server}/docker-mcp-registry/{server.yaml,tools.json,readme.md}`.
4. `task build -- --tools homelab-mcp-platform` to validate.
5. `task catalog -- homelab-mcp-platform` then `docker mcp catalog import $PWD/catalogs/homelab-mcp-platform/catalog.yaml` to test in MCP Toolkit.
6. Open PR to `docker/mcp-registry`. CODEOWNERS routes review to
   `@docker/ai-tools-team`. PR is squash-merged by Docker; entry goes
   live within 24h.
7. Decommission step (if ever needed): open a removal PR to
   `docker/mcp-registry` deleting `servers/homelab-mcp-{server}/`.

**License:** by submitting to `docker/mcp-registry`, the
`server.yaml`/`tools.json`/`readme.md` content (NOT our server source code)
is licensed MIT per the registry's CONTRIBUTING.md. Our image is published
under whatever license we choose for the source repo. For MCP catalog
acceptance the source-code license must be permissive (MIT or Apache 2.0
preferred; GPL is **not accepted**).

**Catalog readiness gate (extends G1–G5):**

| Gate | Name | Check |
|------|------|-------|
| **C1** | Catalog assets present | `packages/homelab-mcp-{server}/docker-mcp-registry/{server.yaml,tools.json,readme.md}` exist and pass `task build --tools` locally |
| **C2** | License compatible | Repo `LICENSE` is MIT or Apache 2.0 (not GPL); `server.yaml` `source.project` points at this repo |
| **C3** | Image on Docker Hub | At least one `:v*.*.*` tag of `dragoshont/homelab-mcp-{server}` exists (precondition for `--image dragoshont/...`) |
| **C4** | tools.json matches inventory | Names in `tools.json` are exactly the server's slice from `docs/migration/tool-inventory.json` (set-equality, same rule as G1) |

A server only ships its `docker/mcp-registry` PR after C1–C4 pass in the
phase SDD that owns it.

**Deferred to phase SDDs:**

- Q9 (which category): default to `monitoring` for platform/media,
  `developer-tools` for network/homeauto/control, `monitoring` for the
  bundle. Each phase SDD reconfirms.
- Q10 (Docker-built vs operator-built): default to operator-built
  (`--image dragoshont/...`), keep build pipeline ownership in this repo.
  Re-evaluate per server if we want the Docker-built image's signing /
  SBOM / auto-update benefits more than build control.

### 4.4 Per-server configuration contract (how users wire to their own services)

Every split server must work for users who run **their own** Sonarr/Radarr/UniFi/Plex/etc.,
not just the operator's homelab. This section pins the configuration
contract grounded in the env-var names already used by the source repo
(`mcp/src/homelab_mcp/clients.py` at the pinned commit).

#### Principles

1. **Twelve-factor.** Each service is configured via env vars only — no
   config files baked into the image. Bundle (§4.1) layers a YAML
   wrapper on top but resolves to the same env vars internally.
2. **Optional services.** A service whose env vars are unset is treated
   as "not configured":
   - Tools that depend on it are **registered** (so the inventory still
     matches `tool-inventory.json` for G1) but return a structured
     error `{"error": "service_not_configured", "service": "sonarr",
     "missing": ["SONARR_URL", "SONARR_API_KEY"]}` on call.
   - This avoids "all-or-nothing" deployment: a user with only Plex
     configured still gets the rest of the readonly platform tools.
   - Rationale: keeping registration stable lets the bundle's gates
     B1–B4 keep working without per-deploy inventory recomputation.
3. **No secret leakage.** Env vars carrying credentials (API keys,
   passwords, tokens) are referenced by name in K8s manifests via
   `secretKeyRef`; never logged; redacted from `audit_*` tool output.
4. **Discovery vs. configuration.** Some tools discover their target
   from the runtime environment (kubeconfig file, mounted Docker
   socket). Most service-specific tools (Sonarr/Radarr/Plex/qbt/UniFi/
   etc.) require explicit env-var config because there's no homelab-wide
   service-discovery convention.

#### Env-var contract per server

The names below are the **canonical contract** going forward and are
**identical to what the current monolith already reads**, so the source
repo's existing `apps/platform/mcp-proxy/deployment.yaml` continues to
work unchanged. Phase SDDs lift them into per-server `pyproject.toml`
extras and `homelab-mcp-{server}` Helm/Kustomize manifests.

##### `homelab-mcp-platform`

| Var | Purpose | Required for |
|-----|---------|--------------|
| `KUBECONFIG` | Path to kubeconfig | All `kube_*`, `flux_*` (RO), `gitops_*`, `ingress_*`, `cert_*`, `dns_*` tools |
| `HOMELAB_HOST` | SSH host for `host_*` and `ansible_*` tools | All `host_*`, `ansible_*`, `backup_*` tools |
| `HOMELAB_SSH_KEY` | Path to SSH private key | Same as above |
| `HOMELAB_SSH_USER` | SSH username | Same as above |
| `HOMELAB_MCP_AUDIT_LOG` | Audit log path | All tools (recommended; default `/var/log/homelab-mcp/{server}.log`) |
| `HOMELAB_MCP_READONLY` | Force read-only mode (`true`/`false`) | All tools (defaults true on RO servers) |
| `CF_DNS_API_TOKEN` / `CLOUDFLARE_API_TOKEN` | Cloudflare API token | `cf_*`, `dns_*` tools |
| `CF_ALLOWED_ZONES` | Comma-separated zone allowlist | `cf_*` tools |
| `NETDATA_URL` | Netdata HTTP endpoint | `netdata_*` tool |

##### `homelab-mcp-media`

All Servarr-family services share the `*_URL` + `*_API_KEY` shape used by `clients.py:get_*_config()`:

| Var | Purpose |
|-----|---------|
| `SONARR_URL`, `SONARR_API_KEY` | Sonarr |
| `RADARR_URL`, `RADARR_API_KEY` | Radarr |
| `LIDARR_URL`, `LIDARR_API_KEY` | Lidarr |
| `READARR_URL`, `READARR_API_KEY` | Readarr |
| `MYLAR3_URL`, `MYLAR3_API_KEY` | Mylar3 |
| `PROWLARR_URL`, `PROWLARR_API_KEY` | Prowlarr |
| `QBT_URL`, `QBT_USER`, `QBT_PASS` | qBittorrent (basic-auth WebUI) |
| `PLEX_URL`, `PLEX_TOKEN` | Plex Media Server |
| `MEDIA_LIBRARY_ROOT` | Optional: shared media root for `media_*` tools |
| `CF_DNS_API_TOKEN` | Cloudflare API token (for `cf_*` cross-seed/cross-fork tools) |

##### `homelab-mcp-network`

| Var | Purpose |
|-----|---------|
| `UNIFI_HOST` | UniFi controller hostname/IP |
| `UNIFI_USER`, `UNIFI_PASS` | Local Limited-Admin login (no 2FA) |
| `UNIFI_PORT` | Default `443` |
| `UNIFI_SITE` | Default `default` |

##### `homelab-mcp-homeauto`

| Var | Purpose |
|-----|---------|
| `DIRIGERA_IP` | IKEA DIRIGERA hub IP (lib appends scheme + port) |
| `DIRIGERA_TOKEN` | DIRIGERA token (one-time `generate-token <hub-ip>` to obtain) |
| `HOMEBRIDGE_URL` | Homebridge UI |
| `HOMEBRIDGE_USER`, `HOMEBRIDGE_PASS` | Homebridge UI login |
| `SCRYPTED_URL` | Scrypted endpoint (default `http://scrypted:11080`) |
| `APPLE_TV_DEVICES` | Comma-separated Apple TV device list |

##### `homelab-mcp-control`

The control server consumes the **union of mutating tools across domains**, so
its env-var set is the union of every other server's relevant vars **plus**
the control-server bearer token:

- All Servarr `*_URL` + `*_API_KEY` (used by `*_search_missing`)
- `QBT_URL` + `QBT_USER` + `QBT_PASS` (for `qbt_pause` / `qbt_resume`)
- `PLEX_URL` + `PLEX_TOKEN` (for `plex_maintenance` / `plex_scan_library`)
- `PROWLARR_URL` + `PROWLARR_API_KEY` (for indexer add/remove)
- `KUBECONFIG` (for `kube_image_can_pull`, `kube_restart`)
- `UNIFI_HOST`, `UNIFI_USER`, `UNIFI_PASS` (for block/unblock/wlan/reconnect)
- `DIRIGERA_IP`, `DIRIGERA_TOKEN` (for set_light/set_outlet/set_blind/trigger_scene)
- `APPLE_TV_DEVICES` (for apple_*)
- **`HOMELAB_MCP_CONTROL_TOKEN`** (mandatory bearer token; auth gate per §5)

##### Bundle (§4.1) extends, doesn't replace

The bundle's `bundle.yaml` (§4.1) does NOT introduce new config keys per
service. Each `servers.{name}` block resolves its environment by:
1. Inheriting from the process environment as if running standalone, AND
2. Allowing override per-server via `env:` block in `bundle.yaml`:

```yaml
servers:
  media:
    enabled: true
    env:
      SONARR_URL: http://my-sonarr.lan:8989
      SONARR_API_KEY: '${oc.env:SONARR_API_KEY}'   # OmegaConf-style env interpolation
```

This lets one bundle process run media against a private Sonarr URL
while platform's `KUBECONFIG` points elsewhere, without polluting the
shared environment.

#### Per-tool service-availability detection (Phase 1 deliverable)

Each tool that depends on a service must call a uniform availability
check before doing work:

```python
def _require(svc_name: str, *envs: str) -> Optional[ServiceUnavailable]:
    missing = [e for e in envs if not os.environ.get(e)]
    if missing:
        return ServiceUnavailable(svc_name, missing)
    return None

@mcp.tool()
def sonarr_calendar(...) -> dict:
    if err := _require("sonarr", "SONARR_URL", "SONARR_API_KEY"):
        return err.as_mcp_response()   # structured "service not configured" error
    ...
```

The Phase 1 SDD lifts a single shared `_require` helper into
`homelab-mcp-core` so all 5 servers use the same shape.

#### Catalog readiness gate C5 (extends C1–C4 in §4.3)

Each split server's `docker-mcp-registry/server.yaml`:
- MUST list every env var **declared** by the server's `_require` calls
  in the `config.env:` or `config.secrets:` block.
- MUST mark credentials (anything ending in `_API_KEY`, `_TOKEN`,
  `_PASS`, `_PASSWORD`) as `secrets:` not `env:`.
- MUST set `example` (NEVER `value`) for secret entries so Docker MCP
  Catalog renders the input field but never bakes the value.

The Phase 1 SDD validates C5 by running a script that compares each
server's `_require` env-var calls (AST scan) to its `server.yaml`
config block and fails on any mismatch.

#### Out of scope for Phase 0

- Concrete `_require` helper implementation (Phase 1 in `homelab-mcp-core`).
- Service-discovery automation (k8s service annotations, DNS-SD, mDNS).
  These are user-driven config; we don't try to auto-detect their stack.
- Multi-instance per service (e.g., two Sonarr instances). Today's
  contract is one service URL per env-var name. If multi-instance is
  needed later, the Phase 1+ SDD that introduces it adds `*_2_URL` /
  `*_2_API_KEY` or moves to a JSON list — out of scope here.

### 4.5 Distribution and transport contract (UX-1, UX-2, UX-3, UX-4)

#### 4.5.1 Transports per server

Reference MCP clients (Claude Desktop, VS Code MCP, Cursor, Goose,
Continue) overwhelmingly use **stdio**. Streamable HTTP is necessary for
in-cluster / OpenWebUI deployments. Each split server supports both, and
the choice is at startup:

| Server | Default transport | Both supported? | Notes |
|--------|-------------------|-----------------|-------|
| `homelab-mcp-platform` | stdio | yes | HTTP for in-cluster |
| `homelab-mcp-media` | stdio | yes | HTTP for in-cluster |
| `homelab-mcp-network` | stdio | yes | HTTP for in-cluster |
| `homelab-mcp-homeauto` | stdio | yes | HTTP for in-cluster |
| `homelab-mcp-control` | stdio (BUT see §5) | yes — HTTP **strongly preferred** for production | stdio acceptable only when target services are reachable from the same machine; production deployments use HTTP + bearer token |
| `homelab-mcp-bundle` | HTTP (it's a multi-server demux) | stdio is N/A for bundle (one stdio pair = one MCP session = one server's tools) | bundle is HTTP-only by definition |

**Important reality check:** stdio matters for users who run **all the
target services on the same machine as the MCP client** — for example, a
Plex server, a Sonarr/Radarr stack, and Claude Desktop all on one
homelab box. For deployments where the MCP client (a laptop running
Claude Desktop) is **not** on the same network as the target services,
HTTP is the only working answer; stdio cannot reach
`sonarr.media.svc.cluster.local`.

This is documented prominently in each server's README so users do not
copy the stdio snippet and then ask "why can't it reach my Sonarr."

#### 4.5.2 Distribution shape per server

| Server | PyPI? | Container? | Why |
|--------|-------|-----------|-----|
| `homelab-mcp-platform` | no — container only | yes | Hard system dependencies: `kubectl`, `ssh`, `git`, `flux` CLI. PyPI install would silently break for users without these. |
| `homelab-mcp-media` | yes | yes | Pure HTTP clients (httpx). PyPI is canonical; container is for K8s. |
| `homelab-mcp-network` | yes | yes | Pure HTTP client. |
| `homelab-mcp-homeauto` | yes | yes | Pure HTTP clients + DIRIGERA SDK. |
| `homelab-mcp-control` | no — container only | yes | Same system deps as platform (kube_restart needs `kubectl`). |
| `homelab-mcp-bundle` | yes (limited) AND container (full) | yes | PyPI bundle ships only the PyPI-compatible servers (media + network + homeauto); the container ships all five. README is explicit about which subset PyPI gives you. |

PyPI distribution is a **third workflow** added to §4.2:
`release-pypi.yml` — triggers on the same `release: published` event as
`release-images.yml`, builds wheels via `uv build`, publishes to PyPI
via `pypa/gh-action-pypi-publish` with **trusted publishing**
(no `PYPI_TOKEN` secret required; OIDC-based). Falls back to a token
secret `PYPI_TOKEN` if trusted publishing isn't configured.

#### 4.5.3 Configuration sources and precedence

Three sources, precedence highest first:

1. **CLI flags** (`--sonarr-url`, `--readonly`, `--port`, `--transport`).
   Required for MCP clients that pass config via `args` (Claude Desktop
   pattern). Mechanical mapping: `SONARR_URL` env var ↔ `--sonarr-url`
   flag (lowercase, underscores → dashes).
2. **Environment variables** (per §4.4). Required for K8s and Docker
   Compose deployments where args are awkward.
3. **Config file** (bundle only): `--config bundle.yaml` per §4.1.
   Used for the bundle's per-server enable/disable + per-server env
   override. Single-server entrypoints do NOT load a config file —
   keeps the simple cases simple.

#### 4.5.4 Why no PyPI for platform / control

Stating this once and clearly so it does not become a recurring
question: **the platform and control servers depend on system binaries
that are not Python packages**. `kubectl` is a Go binary. `ssh` and
`git` are typically system-installed. `flux` is a Go binary. Shipping
these via PyPI would either bundle 100MB+ binaries (unmaintainable) or
silently fail for users who don't already have them. **Container is the
right answer.** PyPI install for platform/control is **explicitly
rejected**, not deferred.

#### 4.5.5 Per-client install snippets (delivered in Phase 1 README)

Each server's README ships paste-ready snippets for the major MCP
clients. Phase 1 produces the actual per-server READMEs; the **shape**
is fixed here so clients are not missed:

- Claude Desktop (stdio)
- VS Code MCP (stdio + remote HTTP variant)
- Cursor
- Goose
- Continue
- OpenWebUI (HTTP)
- Generic Streamable HTTP example

The list is taken from the Playwright-MCP README (the gold-standard MCP
README in the ecosystem) and trimmed to clients with non-trivial homelab
overlap. New clients added by Phase N+ SDDs.

### 4.6 Stability contract (UX-5, UX-6)

#### 4.6.1 Versioning

- Each Python package (`homelab-mcp-{server}`, `homelab-mcp-core`,
  `homelab-mcp-bundle`) is independently versioned with **semver
  (MAJOR.MINOR.PATCH)**.
- The set of **tool names** registered by a server is part of its
  public API. Renaming or removing a tool is a **MINOR** bump with a
  one-minor deprecation window; a hard removal without a deprecation
  cycle is a **MAJOR** bump.
- Adding a new tool is a **MINOR** bump (additive, non-breaking).
- Bug fixes and internal refactors are **PATCH**.
- Container tags follow the package version exactly:
  `dragoshont/homelab-mcp-media:1.4.2`. Floating `:1`, `:1.4`, and
  `:latest` tags are also published per major / minor / latest-release.
- The **bundle** version tracks the highest of its constituent
  packages' MAJOR; bumping any constituent's MAJOR bumps the bundle's
  MAJOR.

Each server-package's `CHANGELOG.md` is mandatory and follows
[Keep a Changelog](https://keepachangelog.com).

#### 4.6.2 Tool deprecation policy

Renaming or removing a tool requires:

1. The new tool name registered alongside the old in the next MINOR
   release. Both call into the same implementation.
2. The old tool's docstring prepended with `[deprecated, use X
   instead, removal target: vN.0.0]`.
3. A `WARNING` log entry every time the deprecated tool is invoked.
4. A `CHANGELOG.md` entry in the deprecating release noting the
   deprecation and the removal target.
5. Removal in the next MAJOR (no exceptions; no in-MINOR removals).

Reasoning: any user with an MCP client config referencing tool X cannot
fix their config until they notice it broke. The deprecation cycle gives
them at least one MINOR release of warnings.

#### 4.6.3 Error envelope contract

Every tool — across all 5 servers + bundle — returns either success
data or a structured error of this exact shape:

```json
{
  "error": {
    "code": "service_not_configured",
    "service": "sonarr",
    "message": "human-readable summary",
    "details": {"missing_config": ["SONARR_URL", "SONARR_API_KEY"]},
    "retryable": false
  }
}
```

Required fields: `code` (snake_case identifier), `message` (one
sentence). Optional: `service`, `details`, `retryable`.

Required `code` values (all servers):

| Code | Meaning | retryable |
|------|---------|-----------|
| `service_not_configured` | Required env var unset; tool is registered but cannot run | false |
| `service_unreachable` | Network failure / timeout / DNS / connection refused | true |
| `auth_failed` | Service rejected credentials | false |
| `permission_denied` | Service was reached but the configured credential lacks access | false |
| `not_found` | Requested resource does not exist | false |
| `invalid_input` | Tool arguments failed validation | false |
| `rate_limited` | Upstream service rate-limited us | true |
| `internal_error` | Bug in the MCP server itself | false |
| `readonly_violation` | Mutating tool called on a read-only server (per §6 G2) | false |

Tools MUST NOT raise unhandled exceptions back to the MCP transport.
Phase 1 lifts a shared `with_error_envelope()` decorator into
`homelab-mcp-core`.

#### 4.6.4 Health and readiness endpoints (HTTP transport only)

Each server's HTTP transport additionally exposes:

- `GET /healthz` — process is alive. Returns `200 {"status":"ok"}`.
  No service connectivity check.
- `GET /readyz` — process is ready to serve. Returns `200` if all
  configured services responded to a quick probe in the last 30 seconds,
  `503` with a JSON list of unreachable services otherwise.
- `GET /version` — returns `{"package": "homelab-mcp-platform",
  "version": "1.4.2", "tools": 51, "commit": "<sha>"}`.

These are not MCP tools (not in the registered set; not in
`tool-inventory.json`). They are HTTP-only operational endpoints. The
stdio transport has no equivalent — the process is alive iff the stdio
pair is open.

#### 4.6.5 Telemetry

**No telemetry by default.** Phase 1 ships zero outbound metrics. The
audit log written locally is the only observation surface.

A future SDD MAY add an opt-in OpenTelemetry export (anonymous tool-
invocation counts and latency histograms; never tool arguments, never
return values, never service URLs). It is out of scope for Phase 0 / 1
and **must not** ship enabled by default.

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
- **Q9 (Docker MCP Catalog category — resolved in §4.3):** default to
  `monitoring` for platform/media/bundle, `developer-tools` for
  network/homeauto/control. Each phase SDD reconfirms its category before
  opening the `docker/mcp-registry` PR.
- **Q10 (Docker-built vs operator-built catalog image — resolved in
  §4.3):** default to operator-built (catalog entry uses
  `image: dragoshont/homelab-mcp-{server}`). Keeps build pipeline
  ownership in this repo. Reconsider per server if Docker's signing /
  SBOM / auto-update become more valuable than build control.
- **Q11 (GitHub MCP Registry submission — deferred to Phase 1+):**
  [github.com/mcp](https://github.com/mcp) is a fourth distribution
  channel parallel to the Docker MCP Catalog (§4.3), targeting GitHub
  Copilot users with an in-UI install button. Submission mechanics
  (registry-repo PR vs `.well-known/mcp` auto-discovery vs other) are
  not researched here; the per-server SDD that owns the first listing
  fetches the current submission contract and either lands assets in
  `packages/homelab-mcp-{server}/github-mcp-registry/` (mirroring §4.3's
  layout) or in this repo's `.well-known/` directory, whichever the
  registry requires. License/test gates equivalent to C1–C4 apply.


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
