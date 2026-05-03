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

The image is a single monolith running a native FastAPI HTTP transport
on port 8080. It exposes **three surfaces from one process**:

- `POST /mcp/*` — native [Streamable HTTP MCP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#streamable-http)
- `GET /openapi.json` + `POST /<tool_name>` — `mcpo`-compatible OpenAPI
  tool-server (consumed by OpenWebUI's `TOOL_SERVER_CONNECTIONS`)
- `GET /healthz` + `GET /metrics` — K8s probes + Prometheus

So it can be consumed by anything that speaks MCP, OpenAPI, or plain
`curl`. Stdio MCP is also available via `--entrypoint homelab-mcp`
(used by the SSH host wrapper).

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
  ghcr.io/dragoshont/homelab-mcp:main
```

Three protocol surfaces come up on port 8080 from one process:

| Path | Purpose |
| --- | --- |
| `POST /mcp` | Native MCP Streamable HTTP — for VS Code, Copilot, Claude Desktop |
| `GET /openapi.json` + `POST /<tool_name>` | mcpo-compatible OpenAPI mirror — for OpenWebUI |
| `GET /healthz`, `GET /metrics` | K8s probes + Prometheus |

Stdio MCP is also available with `--entrypoint homelab-mcp` for SSH-tunneled clients.

### Optional: bearer auth

If you set `HOMELAB_MCP_HTTP_TOKEN=<some-secret>`, requests to `/mcp`,
`/openapi.json`, and `POST /<tool>` require
`Authorization: Bearer <some-secret>`. `/healthz` and `/metrics` stay
open so probes never depend on secret rotation. Leave the env var unset
for trusted-network deployments (the homelab default — auth is added at
the network edge, e.g. Cloudflare Access).

### Use it from a client

#### VS Code (`mcp.json`)

VS Code (with GitHub Copilot or the MCP extension) reads
`.vscode/mcp.json`. Direct HTTP transport:

```json
{
  "servers": {
    "homelab": {
      "type": "http",
      "url": "http://homelab-mcp.local:8080/mcp"
    }
  }
}
```

With auth:

```json
{
  "servers": {
    "homelab": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer ${input:homelab_token}" }
    }
  },
  "inputs": [
    { "id": "homelab_token", "type": "promptString", "password": true,
      "description": "homelab MCP bearer token" }
  ]
}
```

If the host can't reach the container directly (e.g. it's only on a
private cluster), wrap it over SSH with stdio:

```json
{
  "servers": {
    "homelab": {
      "type": "stdio",
      "command": "ssh",
      "args": ["homelab", "docker", "run", "--rm", "-i",
               "--entrypoint", "homelab-mcp",
               "ghcr.io/dragoshont/homelab-mcp:main"]
    }
  }
}
```

#### GitHub Copilot CLI

Copilot's CLI reads the same VS Code `mcp.json`. After adding the
server, run `copilot` and the tools appear under `/tools homelab`.
You can also register globally in `~/.config/github-copilot/mcp.json`.

#### Claude Desktop

Edit `claude_desktop_config.json` (Settings → Developer →
Edit Config):

```json
{
  "mcpServers": {
    "homelab": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://homelab-mcp.local:8080/mcp"]
    }
  }
}
```

`mcp-remote` is the standard bridge that lets Claude Desktop (which
speaks stdio MCP only) talk to a Streamable-HTTP server. Add
`--header "Authorization:Bearer <token>"` after the URL if auth is on.
Restart Claude Desktop for the config to apply.

#### OpenWebUI (`TOOL_SERVER_CONNECTIONS`)

OpenWebUI uses the OpenAPI mirror, declared as a deployment env var
(do **not** add it through the UI — it persists in the PVC and gets
wiped on PVC recreation):

```json
[
  {
    "url": "http://homelab-mcp:8080",
    "path": "openapi.json",
    "auth_type": "none",
    "key": "",
    "config": { "enable": true, "access_control": null },
    "info": { "name": "homelab", "description": "homelab tools" }
  }
]
```

If auth is on, set `auth_type: "bearer"` and put the token in `key`.
The 128-tool function-calling cap on most chat models is handled with
OpenWebUI's `function_name_filter_list` (use `!tool_name` entries to
block, e.g. `"!unifi_block,!kube_restart,..."`).

#### Plain `curl`

```bash
# List tools (mcpo-compatible OpenAPI doc)
curl -s http://homelab-mcp.local:8080/openapi.json | jq '.paths | keys'

# Call a tool
curl -s -X POST http://homelab-mcp.local:8080/host_status \
  -H 'content-type: application/json' -d '{}'

# With auth
curl -s -X POST http://mcp.example.com/host_status \
  -H 'authorization: Bearer <token>' \
  -H 'content-type: application/json' -d '{}'
```

### Tool catalog

All 133 tools ship in **this** image — there are no separate per-domain
images or sidecars (the bundled monolith is intentional; see "Repository
layout" below). Group breakdown:

| Server group | Read tools | Mutating tools | Examples |
| --- | ---:| ---:| --- |
| `platform` (kube, host, flux, audit, observability) | 51 | 0 | `kube_pods`, `kube_log_errors`, `flux_status`, `host_smart`, `netdata_query`, `audit_tail` |
| `media` (Sonarr, Radarr, Lidarr, Readarr, Mylar3, Prowlarr, qBittorrent, Plex, Bazarr, Cloudflare DNS) | 30 | 0 | `sonarr_queue`, `qbt_torrents`, `plex_now_playing`, `cf_dns_list` |
| `control` (mutating ops across all domains) | 0 | 29 | `qbt_pause`, `radarr_delete`, `kube_restart`, `flux_reconcile`, `unifi_block`, `apple_play_pause`, `dirigera_set_light` |
| `homeauto` (DIRIGERA, Apple TV, Homebridge, Scrypted) | 16 | 0 | `dirigera_devices`, `dirigera_lights`, `dirigera_sensors`, `apple_devices`, `apple_now_playing`, `homebridge_accessories` |
| `network` (UniFi) | 7 | 0 | `unifi_clients`, `unifi_devices`, `unifi_health` |

Full machine-readable inventory:
[`docs/migration/tool-inventory.json`](docs/migration/tool-inventory.json).

### Enabling / disabling individual tools

There are two layers of control:

**1. Server-side: which tools the server is willing to execute.**

| Switch | Effect |
| --- | --- |
| `HOMELAB_MCP_READONLY=true` (default) | Refuses every mutating tool (returns a structured `read_only` error). The 104 read tools still work. |
| `HOMELAB_MCP_READONLY=false` | All 133 tools are callable. Required for write paths like `qbt_pause`, `radarr_delete`, `dirigera_set_light`, etc. |
| Per-service env vars unset (e.g. `SONARR_URL` not set) | Tools for that service return a structured `service_not_configured` error. No crash. This is how you "disable" a whole upstream — just leave its env vars unset. |
| `CF_ALLOWED_ZONES` (CSV) | Cloudflare DNS write tools refuse any zone not in the allowlist. |

**2. Client-side: which tools the LLM is allowed to see.**

Most chat models cap function-calling at ~128 tools. OpenWebUI's
`TOOL_SERVER_CONNECTIONS` accepts a `function_name_filter_list` field
that prefixes blocked tools with `!`:

```json
[
  {
    "url": "http://homelab-mcp:8080",
    "path": "openapi.json",
    "auth_type": "none",
    "config": {
      "enable": true,
      "function_name_filter_list":
        "!dirigera_set_state,!unifi_block,!kube_restart,!host_reboot_required"
    },
    "info": { "name": "homelab" }
  }
]
```

Conventions:

- `!name` blocks; absence of a `!` line means allow.
- An empty `function_name_filter_list` allows everything.
- Mix-and-match: enable read-only DIRIGERA but hide the writes:
  ```
  !dirigera_set_light,!dirigera_set_outlet,!dirigera_set_scene,!dirigera_set_device,!dirigera_set_state
  ```

For native MCP clients (VS Code, Claude Desktop, Copilot CLI) there is
no per-tool block list at the server — the model sees everything the
server advertises. To narrow that surface, run a second instance of the
image with the corresponding upstream env vars omitted.

### Example: enabling DIRIGERA (IKEA smart-home hub)

DIRIGERA is configured via two env vars:

```bash
docker run --rm -p 8080:8080 \
  -e DIRIGERA_HUB_IP=192.168.1.42 \
  -e DIRIGERA_TOKEN=<paired-token> \
  ghcr.io/dragoshont/homelab-mcp:main
```

To get the token, run the helper from the [`dirigera`](https://github.com/Leggin/dirigera)
package once and press the action button on the hub when prompted:

```bash
pip install dirigera
generate-token 192.168.1.42
```

After that, the eleven `dirigera_*` tools (six read, five write) become
live. With `HOMELAB_MCP_READONLY=true` only the read tools execute; the
writes return `read_only`. Typical OpenWebUI block list to keep just the
read surface:

```
!dirigera_set_light,!dirigera_set_outlet,!dirigera_set_scene,!dirigera_set_device,!dirigera_set_state
```

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
| `HOMELAB_MCP_HTTP_HOST` / `HOMELAB_MCP_HTTP_PORT` | Bind for the FastAPI app (default `0.0.0.0:8080`) |
| `HOMELAB_MCP_HTTP_TOKEN` | Optional bearer token. When set, gates `/mcp`, `/openapi.json`, and `POST /<tool>`. `/healthz` + `/metrics` remain open. Whitespace-only is fail-closed (server refuses to start). |

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

The bundle image carries all 133 tools in a single process. Phase 1+
adds **per-domain images** along trust boundaries so a client can
register a smaller surface and mutating tools live behind their own
endpoint. The full plan is in
[`docs/migration/phase-1-plus-plan.md`](docs/migration/phase-1-plus-plan.md).

| Server | Role | Tools | Mutating | Status |
|---|---|---:|---:|---|
| `homelab-mcp-bundle` (today's `homelab-mcp`) | All five domains in one process — drop-in monolith | 133 | 29 | ✅ Shipping (Phase 0) |
| `homelab-mcp-platform` | Read-only — Kubernetes, host, FluxCD, image registry, Cloudflare DNS, Netdata | 51 | 0 | 🚧 Phase 1.1+ |
| `homelab-mcp-media` | Read-only — Sonarr / Radarr / Lidarr / Readarr / Mylar3 / Prowlarr / qBittorrent / Plex | 30 | 0 | 🚧 Phase 1.1+ |
| `homelab-mcp-network` | Read-only — UniFi inventory & status | 7 | 0 | 🚧 Phase 1.1+ |
| `homelab-mcp-homeauto` | Read-only — DIRIGERA, Homebridge, Scrypted, Apple TV | 16 | 0 | 🚧 Phase 1.1+ |
| `homelab-mcp-control` | **Opt-in** — all mutating actions across domains | 29 | **29** | 🚧 Phase 1.5 |

**Phase 1.0 (server.py refactor) is complete:** the 3,319-line monolith
is now split into a 35-line orchestrator (`server.py`) plus shared
runtime (`_runtime.py`) plus five domain modules
(`tools/{platform,media,network,homeauto,control}.py`). Tool source is
byte-faithful, verified by `tools/verify_lift.py` (G-5).

**Phase 1.1+ adds the per-domain images.** Each is built from the same
`mcp/` package via `mcp/Dockerfile.domain` with a `DOMAIN` build-arg.
The `homelab_mcp.entrypoints` module exposes `run_domain("network")`
which imports only one `tools/{domain}` module before starting the
server, so the resulting per-domain image exposes exactly that
domain's tools (and no others).

Per-domain release tags are `<domain>-vX.Y.Z` (e.g. `network-v0.1.0`)
and publish to both registries with multiple tags per release:

- `ghcr.io/dragoshont/homelab-mcp-{domain}:{version}`
- `ghcr.io/dragoshont/homelab-mcp-{domain}:v{version}`
- `ghcr.io/dragoshont/homelab-mcp-{domain}:latest` (non-prereleases only)
- `docker.io/hserver/homelab-mcp-{domain}:{version}`
- `docker.io/hserver/homelab-mcp-{domain}:v{version}`
- `docker.io/hserver/homelab-mcp-{domain}:latest` (non-prereleases only)

Set-equality with the 133/29/104 totals is enforced by
`tools/verify_lift.py` on every change.

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
