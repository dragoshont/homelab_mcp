# homelab_mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server that
exposes a Kubernetes homelab as **133 tools** to LLM clients
([Claude Desktop](https://claude.ai/download), VS Code, Cursor, Goose,
Continue, [OpenWebUI](https://openwebui.com), and any other MCP-aware client).

It is the backend that powers a chat experience like:

> *"What's the queue on Sonarr?"*
> *"Pause qBittorrent."*
> *"Are there any pods crashing in `default`?"*
> *"Recently added movies in Plex."*
> *"Block this client on the WiFi."*

## What it does

The server reads from and (with explicit opt-in) controls a typical homelab:

| Domain | Examples of what the LLM can do |
|---|---|
| **Kubernetes** | List pods, describe a pod, fetch logs, find crashlooping pods, OOM events, top pods, validate ingress, find services with no endpoints, image-pull dry-run, FluxCD status / reconcile / suspend / resume, drift report. |
| **Host OS** | Disk usage, SMART, mount + NFS status, package upgradability, reboot required, journal, dmesg errors, failed systemd units, OS version, security audit. |
| **Media stack** | Sonarr / Radarr / Lidarr / Readarr / Mylar3 / Prowlarr / qBittorrent / Plex — health, queues, calendars, missing items, manual search trigger, library scan, recently added, active sessions. |
| **Networking** | UniFi clients, devices, top talkers, port-forwards, WLANs, block / unblock / reconnect a client, set WLAN. |
| **Home automation** | DIRIGERA lights / blinds / outlets / sensors / scenes (read + control), Homebridge accessories + plugins, Scrypted status, Apple TV (now-playing, remote, scan, run shortcut). |
| **Cloudflare DNS** | List / get / upsert / delete records (zone allowlist enforced). |
| **Observability** | Netdata queries, audit log tail. |

Every mutating tool is gated by `HOMELAB_MCP_READONLY=true` by default;
flip it to `false` only on a dedicated control endpoint to actually
execute writes. Read-only inspection works without any credential except
the read tokens for the upstream APIs.

## Quickstart

The image is a single monolith that wraps a stdio MCP server with
[`mcpo`](https://github.com/open-webui/mcpo), so it speaks **OpenAPI over
HTTP** and can be consumed by anything that talks HTTP — including
OpenWebUI, agentic frameworks, and `curl`.

### Pull the image

```bash
# GHCR (preferred)
docker pull ghcr.io/dragoshont/homelab-mcp:v1.1.1

# or Docker Hub mirror
docker pull hserver/homelab-mcp:v1.1.1
```

Tags published per release: `:<version>`, `:v<version>`, `:latest`.
Plus rolling `:main` and `:sha-<short>` on every push to `main` (GHCR only).

### Run it

```bash
docker run --rm -p 8080:8080 \
  -e SONARR_URL=http://sonarr:8989 -e SONARR_API_KEY=... \
  -e RADARR_URL=http://radarr:7878 -e RADARR_API_KEY=... \
  -e PLEX_URL=http://plex:32400  -e PLEX_TOKEN=... \
  -e QBT_URL=http://qbittorrent:8080 -e QBT_USER=... -e QBT_PASS=... \
  # ...one URL+credential pair per upstream you want to expose...
  ghcr.io/dragoshont/homelab-mcp:v1.1.1
```

OpenAPI surface is then on `http://localhost:8080/openapi.json` (133 paths).

### Wire it into OpenWebUI

OpenWebUI consumes the proxy declaratively via `TOOL_SERVER_CONNECTIONS`:

```json
[
  {
    "url": "http://homelab-mcp:8080",
    "path": "/openapi.json",
    "auth_type": "none",
    "config": { "enable": true, "access_control": null },
    "info": { "name": "homelab", "description": "homelab tools" }
  }
]
```

Set this as a deployment env var (don't add the connection through the
UI — it gets wiped on PVC recreation).

### Configuration contract

All config is via env vars. **No homelab specifics are hardcoded** — the
server is public-safe and reusable. Required env vars at startup:

| Env var | Purpose |
|---|---|
| `HOMELAB_HOST`, `HOMELAB_SSH_USER`, `HOMELAB_SSH_KEY` | SSH target for `host_*` tools |
| `HOMELAB_INGRESS_DOMAIN`, `HOMELAB_INGRESS_IP` | Used by `ingress_probe` for SSRF-bounded HTTPS probes |
| `CF_ALLOWED_ZONES` (CSV) | Cloudflare DNS write allowlist; tools refuse zones outside this set |
| `CF_DEFAULT_ZONE` | Default zone for read tools |
| `HOMELAB_MCP_READONLY` | `true` to refuse all mutating tools (default) |
| `HOMELAB_MCP_AUDIT_LOG` | Path to the append-only audit log |

Per-service URL + API-key env vars (e.g., `SONARR_URL` + `SONARR_API_KEY`)
are optional — tools whose service isn't configured return a structured
`service_not_configured` error instead of crashing the server.

Tool inventory and credential matrix:
[`docs/migration/tool-inventory.json`](docs/migration/tool-inventory.json)
(133 tools, 104 read-only, 29 mutating). The inventory is enforced by
[`tools/validate_inventory.py`](tools/validate_inventory.py) and
[`tools/verify_lift.py`](tools/verify_lift.py) on every change.

## Status

- **v1.1.1** is the first public release (2026-05-01). It runs the
  133-tool monolith currently powering the author's homelab.
- The image is the byte-faithful lift of
  [`dragoshont/homelab/mcp/`](https://github.com/dragoshont/homelab) at
  commit `71129a278e69`, after a Phase 0.4 refactor that removed all
  hardcoded homelab-specific values.
- 7 known inherited bugs are catalogued in
  [`docs/migration/inherited-tool-bugs.md`](docs/migration/inherited-tool-bugs.md);
  none affect the read path of the most-used tools, and each will be
  fixed with a regression test in the upcoming domain-split phases.

## Roadmap (Phase 1+)

The current image is one big monolith with all 133 tools in a single
process. Future work splits this along trust boundaries so a client can
register a smaller surface and so mutating tools live behind their own
endpoint:

| Server | Role | Tools | Mutating |
|---|---|---:|---:|
| `homelab-mcp-platform` | Read-only — Kubernetes, host, FluxCD, image registry, Cloudflare DNS, Netdata | 51 | 0 |
| `homelab-mcp-media` | Read-only — Sonarr / Radarr / Lidarr / Readarr / Mylar3 / Prowlarr / qBittorrent / Plex | 30 | 0 |
| `homelab-mcp-network` | Read-only — UniFi inventory & status | 7 | 0 |
| `homelab-mcp-homeauto` | Read-only — DIRIGERA, Homebridge, Scrypted, Apple TV | 16 | 0 |
| `homelab-mcp-control` | **Opt-in** — all mutating actions across domains | 29 | **29** |
| `homelab-mcp-bundle` | Single process running any subset via config (drop-in for today's monolith) | (sum) | (sum) |

Set-equality with the current 133/29/104 totals is enforced as a CI gate
across the split (see [`docs/migration/migration-plan.md`](docs/migration/migration-plan.md)).

Each server will publish on the same channels the monolith uses today:
`ghcr.io/dragoshont/homelab-mcp-{server}:<ver>`,
`hserver/homelab-mcp-{server}:<ver>`, and (where pure-Python) PyPI for
`uvx`/`pip install`.

## Repository layout

```
homelab_mcp/
├── README.md                ← this file
├── LICENSE                  ← MIT
├── mcp/                     ← v1.1.1 monolith sources (lifted from dragoshont/homelab)
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── src/homelab_mcp/     ← server.py + clients.py + audit.py + policy.py + ...
│   ├── tests/               ← 136 tests, hardening contracts
│   └── .lift-manifest.json  ← per-file SHA-256, source commit pin
├── tools/                   ← validate_inventory.py (G-2), verify_lift.py (G-5),
│                              lift_phase_0_5.py (source-lift script)
├── docs/
│   ├── migration/           ← migration plan, tool inventory, inherited-bugs catalog
│   └── TSGs/Operations/     ← TSGs for operational procedures
├── .github/
│   ├── actions/build-mcp-image/   ← composite action (qemu + buildx + push)
│   ├── workflows/build-monolith.yml   ← push-to-main → GHCR
│   └── workflows/release-monolith.yml ← v*.*.* tag → GHCR + Docker Hub
└── out/Rivet/sdd/           ← per-phase SDD evidence
```

## Security

- **Mutating tools are gated.** `HOMELAB_MCP_READONLY=true` (default)
  rejects any write tool with a structured `rejected_readonly` audit
  entry. Treat the read-only image as the default deployment; only flip
  the flag on a dedicated control endpoint with stricter network
  isolation.
- **No persistent token in the image.** All credentials are env-injected
  at runtime; no defaults, no homelab specifics, no `latest` tags
  pinned in the running deployment.
- **SSRF-bounded probes.** `ingress_probe` and similar tools refuse hosts
  outside `HOMELAB_INGRESS_DOMAIN`. Cloudflare DNS writes refuse zones
  outside `CF_ALLOWED_ZONES`.
- **Audit log.** Every mutating call is appended to
  `HOMELAB_MCP_AUDIT_LOG` with a deterministic 4-column schema.
- For security issues, open a private advisory:
  https://github.com/dragoshont/homelab_mcp/security/advisories/new

## License

MIT — see [LICENSE](LICENSE).
