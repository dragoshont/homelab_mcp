# homelab_mcp

Modular [Model Context Protocol](https://modelcontextprotocol.io) servers for
homelab operations: Kubernetes & host inventory, media stack (Sonarr / Radarr
/ Plex / qBittorrent), networking (UniFi), home automation (DIRIGERA, Apple,
Homebridge, Scrypted), and a clearly separated control surface for mutating
actions.

> **Status:** Phase 0 — migration plan only. Server packages, container images,
> and PyPI distributions are introduced by per-phase SDDs after this PR
> merges. The plan is in [docs/migration/migration-plan.md](docs/migration/migration-plan.md).

## What this repo will host

Five domain-scoped MCP servers + one all-in-one bundle, replacing a single
133-tool monolith currently hosted in [`dragoshont/homelab`](https://github.com/dragoshont/homelab):

| Server | Role | Tools | Mutating |
|--------|------|------:|---------:|
| `homelab-mcp-platform` | Read-only — Kubernetes, host, FluxCD, image registry, Cloudflare DNS, Netdata | 51 | 0 |
| `homelab-mcp-media` | Read-only — Sonarr, Radarr, Lidarr, Readarr, Mylar3, Prowlarr, qBittorrent, Plex | 30 | 0 |
| `homelab-mcp-network` | Read-only — UniFi inventory & status | 7 | 0 |
| `homelab-mcp-homeauto` | Read-only — DIRIGERA, Homebridge, Scrypted, Apple TV | 16 | 0 |
| `homelab-mcp-control` | **Opt-in** — all mutating actions across domains | 29 | **29** |
| `homelab-mcp-bundle` | Single process running any subset of the five via config | (sum) | (sum) |

Sum: 133 tools = 104 read-only + 29 mutating. Set-equality with the source
monolith is enforced by [`tools/validate_inventory.py`](tools/validate_inventory.py).

## Why split

The existing monolith exposes 133 tools as a single MCP endpoint. Splitting
along trust boundaries gives:

- **Smaller registered surface per client** — connect to `homelab-mcp-media`
  and the model sees 30 tools, not 133.
- **Read-only by default** — mutating actions live on a separate, opt-in
  control endpoint with stricter auth.
- **Per-domain operational independence** — a bug in one domain cannot crash
  the others; images can roll independently.
- **Open-source-friendly packaging** — domains a community wants to reuse
  (media, kubernetes observation) can be pulled in isolation.

## Distribution channels (planned)

| Channel | Cadence | Audience |
|---------|--------|----------|
| `ghcr.io/dragoshont/homelab-mcp-{server}` | every push to `main` | CI artifact, dev / latest |
| `dragoshont/homelab-mcp-{server}` (Docker Hub) | GitHub release `v*.*.*` | community pull |
| [Docker MCP Catalog](https://hub.docker.com/mcp) | manual, per server, post-first-release | Docker Desktop MCP Toolkit users |
| PyPI `homelab-mcp-{server}` (where pure-Python) | release | `pip install` / `uvx` users |

See [`out/Rivet/sdd/homelab-mcp-migration-plan/design.md`](out/Rivet/sdd/homelab-mcp-migration-plan/design.md)
§4.2–§4.5 for the full strategy.

## How users will connect (planned)

Each server supports two transports:

| Transport | Use case |
|-----------|----------|
| **stdio** | MCP clients running on the same machine as the target services (Claude Desktop, VS Code, Cursor, Goose, Continue). Default. |
| **Streamable HTTP** | In-cluster deployment, OpenWebUI, or any remote MCP client. Auth via cluster network policy + (control server) bearer token. |

Configuration is via env vars **or** equivalent CLI flags **or** a YAML
config file (bundle only). Every credential is a secret reference, never
logged. Tools whose target service is not configured return a structured
`service_not_configured` error rather than refusing to start.

Concrete env-var contract per server:
[`design.md` §4.4](out/Rivet/sdd/homelab-mcp-migration-plan/design.md).

## Repository layout (target, post-migration)

```
homelab_mcp/
├── README.md                ← this file
├── LICENSE                  ← MIT
├── docs/migration/          ← Phase 0 plan, inventory, validators (this PR)
├── packages/                ← per-server Python packages (Phase 1+)
│   ├── homelab-mcp-core/
│   ├── homelab-mcp-platform/
│   ├── homelab-mcp-media/
│   ├── homelab-mcp-network/
│   ├── homelab-mcp-homeauto/
│   ├── homelab-mcp-control/
│   └── homelab-mcp-bundle/
├── containers/              ← per-server Dockerfiles (Phase 1+)
├── deploy/                  ← reference K8s manifests (Phase 1+)
├── tools/                   ← repo tooling (validators, helpers)
└── out/Rivet/sdd/           ← SDD evidence (this PR)
```

## Plan documents

- [Migration plan (public-readable)](docs/migration/migration-plan.md)
- [Tool inventory (machine-readable, 133 tools)](docs/migration/tool-inventory.json)
- [Spec](out/Rivet/sdd/homelab-mcp-migration-plan/spec.md) — what & why
- [Design](out/Rivet/sdd/homelab-mcp-migration-plan/design.md) — how
- [Verify contract](out/Rivet/sdd/homelab-mcp-migration-plan/contract.md) — MUST PASS / MUST FAIL
- [Adversarial findings](out/Rivet/sdd/homelab-mcp-migration-plan/as-findings.json) — review of the plan itself

## Contributing

This repo is in Phase 0 of a multi-phase plan. Code contributions land via
per-phase SDDs (Phase 1 = `homelab-mcp-platform` first). Once the first phase
ships, contribution guidelines will be published in `CONTRIBUTING.md`.

## Security

The mutating control server (`homelab-mcp-control`) ships separately from the
read-only servers and requires an explicit bearer token (`HOMELAB_MCP_CONTROL_TOKEN`)
in addition to network-level isolation. Read-only servers do not register any
mutating tool — see [policy](https://github.com/dragoshont/homelab/blob/main/mcp/src/homelab_mcp/policy.py)
in the source repo for the canonical write-tool list.

For security issues, open a private security advisory:
https://github.com/dragoshont/homelab_mcp/security/advisories/new

## License

MIT — see [LICENSE](LICENSE).

