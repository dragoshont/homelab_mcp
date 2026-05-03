# Public `/openapi.json` for tool-server discovery

## Overview

`homelab-mcp` exposes its FastMCP tool registry as an OpenAPI 3.1 document at
`GET /openapi.json` so OpenAPI-aware MCP clients (notably **OpenWebUI**'s
`TOOL_SERVER_CONNECTIONS`) can auto-discover the tool surface. This endpoint
is **public** (no bearer token required) — by contract, on purpose, even when
the deployment IS configured with `HOMELAB_MCP_HTTP_TOKEN`.

The OpenAPI document describes only the API surface (tool names + JSON
Schemas for parameters); it contains no data, no credentials, and grants no
execution capability. Tool **execution** (`POST /<tool>`) and the MCP
Streamable HTTP transport (`/mcp/*`) remain bearer-gated.

This is the same boundary the previous tool-server (`mcpo`) drew, and it
mirrors how every major OpenAPI-served service treats its spec endpoint
(GitHub, Stripe, Microsoft Graph all expose OpenAPI/Swagger publicly while
gating data with API keys).

## Architecture

```
                   +------------------------------------+
                   |   homelab-mcp FastAPI app          |
                   |                                    |
    OpenWebUI ---> |   GET  /openapi.json   ---- public |  spec discovery
                   |   GET  /healthz        ---- public |  k8s liveness
                   |   GET  /metrics        ---- public |  prom scrape
                   |                                    |
   client+token -> |   POST /<tool>         ---- auth   |  execution
                   |   *    /mcp/*          ---- auth   |  MCP transport
                   +------------------------------------+
```

The auth middleware (`mcp/src/homelab_mcp/http_app.py`) consults a
module-level `PUBLIC_ENDPOINTS` set:

```python
PUBLIC_ENDPOINTS: frozenset[tuple[str, str]] = frozenset({
    ("/healthz", "GET"),    ("/healthz", "HEAD"),
    ("/metrics", "GET"),    ("/metrics", "HEAD"),
    ("/openapi.json", "GET"),  ("/openapi.json", "HEAD"),
})
```

Each entry is a `(canonical_path, METHOD)` tuple. The method dimension is
what defends `POST /openapi.json` from bypassing auth (MF-8 in the SDD
contract). The middleware canonicalises the request path with a single
`rstrip("/")` before comparing, so `/openapi.json/` works behind ingress
controllers that append a trailing slash.

### Path-confusion defence (MF-7)

A request line like `GET /openapi.json/../mcp HTTP/1.1` arrives at the
middleware with `request.url.path == "/openapi.json/../mcp"` (literal —
Starlette does not collapse `..` segments). Exact-match against
`PUBLIC_ENDPOINTS` rejects it, so the request falls through to auth and
gets a 401. A regression of this property would be caught by
`test_path_traversal_request_path_is_literal`, which probes Starlette's
behaviour directly.

## Configuration

No new environment variables. The existing `HOMELAB_MCP_HTTP_TOKEN`:

- **set + non-empty** -> `POST /<tool>` and `/mcp/*` require
  `Authorization: Bearer <token>`; `/openapi.json` stays public.
- **unset** (or empty) -> no auth on any endpoint; `/openapi.json` still
  reachable.
- **whitespace-only** -> fail-closed; the server refuses to start.

If a future deployment requires private spec discovery, the recommended
controls are network-level (NetworkPolicy / Traefik IPAllowList /
Cloudflare Access), not application-level.

## Usage

### From OpenWebUI

The `homelab` entry in `TOOL_SERVER_CONNECTIONS` (in the consumer's
helmrelease) requires no auth on discovery:

```yaml
- url: http://homelab-mcp-proxy.default.svc.cluster.local:8080
  path: openapi.json
  auth_type: none      # spec discovery is public by contract
  key: ""
  config:
    enable: true
```

Tool execution from OpenWebUI is performed by the OpenWebUI backend on
behalf of the user. If the homelab-mcp deployment requires a token,
OpenWebUI's tool-execution path needs the token configured separately
(this is a separate concern — `auth_type: bearer` on the connection
makes OpenWebUI send the bearer on POST calls but NOT on the discovery
GET; that asymmetry is intentional).

### From `curl`

```bash
# Discovery (public)
curl -sf http://homelab-mcp-proxy:8080/openapi.json | jq '.paths | keys | length'
# 109 (varies with bundle)

# Execution (bearer required)
curl -sf -X POST http://homelab-mcp-proxy:8080/host_status \
  -H "Authorization: Bearer $HOMELAB_MCP_HTTP_TOKEN" \
  -d '{}'
```

## Troubleshooting

- **OpenWebUI's tool list is empty** — confirm `/openapi.json` returns 200
  unauthenticated:
  ```bash
  kubectl exec openwebui-open-webui-0 -c open-webui -- \
    curl -sf -o /dev/null -w "%{http_code}\n" \
    http://homelab-mcp-proxy.default.svc.cluster.local:8080/openapi.json
  ```
  Should print `200`. If it prints `401`, the deployment is running an
  older image without this change — bump the image tag in
  `apps/platform/mcp-proxy/deployment.yaml`.

- **Tool list still empty after image bump** — OpenWebUI caches the tool
  list inside the `tool_server.connections[*]` entry. Force re-discovery:
  ```bash
  kubectl delete pod openwebui-open-webui-0
  ```
  On boot the env-driven `TOOL_SERVER_CONNECTIONS` re-issues a discovery
  GET against each connection.

- **`/openapi.json` returns 503** — the server's tool-manager couldn't
  enumerate tools at startup. Inspect the body's `error` field and check
  pod logs for the underlying cause (typically a tool-bundle import
  failure).

- **`POST /openapi.json` returns 200** — would be a regression: the
  public bypass should be GET/HEAD only. The frozen-set test
  `test_public_endpoints_constant_is_correct` catches accidental
  loosening; if it fails, investigate the diff against this SDD's
  `contract.md`.

## Related

- SDD: `out/Rivet/sdd/public-openapi/` — contract.md, spec.md, design.md,
  as-findings.json.
- Phase 1 SDD: `out/Rivet/sdd/fastapi-phase1/` — introduced the bearer
  middleware that this SDD partially relaxes.
- Phase 2 SDD: `out/Rivet/sdd/fastapi-phase2/` — introduced the
  `/openapi.json` mirror itself.
- Tests: `mcp/tests/test_openapi_mirror.py` (search for
  `# SDD: public-openapi`).
