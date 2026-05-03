# Verify Contract — public-openapi

Feature: make `/openapi.json` (and trailing-slash variant) **publicly readable** without a bearer token, while keeping `/mcp/*` and `POST /<tool>` strictly token-gated.

## Background

Phase 1 (PR #15) shipped a defense-in-depth bearer middleware that gates **every** path except `/healthz` and `/metrics`. OpenWebUI's `TOOL_SERVER_CONNECTIONS` discovers tool surface by `GET {url}/{path}` where `path = openapi.json`, with `auth_type: none`. With the current code, that GET returns 401 and OpenWebUI silently drops the entry — confirmed in production (`homelab-mcp-proxy:8080/openapi.json -> 401`).

The OpenAPI document only contains tool names + JSON Schema parameter shapes — **no data, no mutations, no auth credentials**. Tool surface is already public via the GitHub README. Tool execution stays auth-gated.

## MUST PASS

1. **MP-1** — `GET /openapi.json` returns 200 + the cached `app.state.openapi_doc` when a `HOMELAB_MCP_HTTP_TOKEN` is configured AND no `Authorization` header is sent.
2. **MP-2** — `GET /openapi.json/` (trailing slash) returns 200 under the same conditions (Ingress controllers may rewrite).
3. **MP-3** — `GET /openapi.json` STILL returns 200 when no token is configured (no behavioural change for unauthenticated deployments).
4. **MP-4** — `GET /openapi.json` still returns 503 + the JSON envelope when `app.state.openapi_mirror_degraded` is True (existing degraded-mirror semantics preserved).
5. **MP-5** — `GET /healthz` and `/metrics` (and trailing-slash variants) remain unauthenticated (no regression).
6. **MP-6** — Existing test suite passes unchanged (no test asserting `/openapi.json` returns 401 without a token); any test that previously asserted 401 must be UPDATED to reflect the new contract, with comment citing this contract entry.

## MUST FAIL (no regression — auth still enforced)

1. **MF-1** — `POST /<tool>` (any registered tool) without bearer header returns 401 + `WWW-Authenticate: Bearer realm="homelab-mcp"`. Token configured.
2. **MF-2** — `POST /<tool>` with wrong bearer returns 401.
3. **MF-3** — `POST /<tool>` with correct bearer executes the tool (200 / tool-defined response).
4. **MF-4** — `GET /mcp` (or any `/mcp/*` sub-path) without bearer returns 401.
5. **MF-5** — `GET /mcp` with correct bearer reaches the FastMCP streamable transport (proxied through, not 401).
6. **MF-6** — `GET /docs`, `/redoc`, or any other path NOT in the public allow-list returns 401 when token configured. (FastAPI's auto-`/docs` is disabled via `docs_url=None`, but defense-in-depth: confirm the bypass list is exact-string match, not prefix match.)
7. **MF-7** — Path-confusion attack: `GET /openapi.json/../mcp` (or any URL where the canonical path is NOT `/openapi.json`) does NOT bypass auth. Comparison must be against the canonicalised request path AFTER URL parsing.
8. **MF-8** — Method confusion: `POST /openapi.json`, `PUT /openapi.json`, `DELETE /openapi.json` are NOT made public — only `GET` (and `HEAD` if Starlette routes it). A POST to `/openapi.json` returns 405 Method Not Allowed (FastAPI default), NOT 200.

## Integration Points

- **homelab_mcp/http_app.py** `auth_mw` — the only enforcement point. Bypass list lives here.
- **homelab_mcp/http_app.py** `_register_openapi_mirror` — registers `GET /openapi.json` route.
- **homelab_mcp/http_app.py** `create_app` — wires middleware before route mount.
- **mcp/tests/test_http_app.py** — test seam: any test asserting 401 on `/openapi.json` without auth must flip to 200.
- **OpenWebUI** consumer: `apps/platform/openwebui/helmrelease.yaml` `TOOL_SERVER_CONNECTIONS[homelab].auth_type = "none"` — REMAINS `none`. No change needed downstream.
- **homelab cluster** `homelab-mcp-proxy` Deployment — pulls the new image tag once published; verify `/openapi.json` returns 200 and OpenWebUI picks it up.

## Out of scope

- Authentication for `/healthz`, `/metrics` — already public, unchanged.
- Adding `/docs` (Swagger UI) — kept disabled. (Reasoning: not needed by OpenWebUI; if added, must be public too OR explicitly token-gated.)
- HMAC / signed-URL auth for `/openapi.json`. Bearer-on-execution is the security boundary; spec discovery is intentionally open.
- IP allow-listing — that's a NetworkPolicy / Ingress concern, not application code.
- Phase 3 public exposure (mcp.hont.ro): out of scope; in-cluster contract only.
