# Design — public-openapi

## Approach

Replace the **closed** allow-list bypass in `auth_mw` (currently a hard-coded 2-path tuple) with a **public-paths set** that's:
1. Defined as a module-level constant so it's discoverable, testable, and grep-able.
2. Compared against the **canonicalised** request path (`request.url.path`) using **exact match after `rstrip("/")`** — never prefix match (closes path-confusion attacks).
3. Method-aware: each public path declares its allowed methods; non-listed methods fall through to auth.

### New module-level structure

```python
# Paths allowed without authentication. Only the listed (path, method)
# combinations bypass the bearer check; everything else is gated.
#
# Tool surface (names + JSON schemas) is intentionally public — it
# describes the API, not data. Tool execution (POST /<tool>) and the
# MCP protocol (/mcp/*) remain bearer-gated.
#
# Why a set of (path, method) tuples, not a flat path set:
#   - Defends against MF-8 (POST /openapi.json must NOT bypass auth).
#   - Makes adding a new public path require an explicit method choice.
#   - Mirrors how OpenAPI itself models authorization (path + operation).
PUBLIC_ENDPOINTS: frozenset[tuple[str, str]] = frozenset({
    ("/healthz", "GET"),
    ("/healthz", "HEAD"),
    ("/metrics", "GET"),
    ("/metrics", "HEAD"),
    ("/openapi.json", "GET"),
    ("/openapi.json", "HEAD"),
})
```

### `auth_mw` change

Pseudo-diff:

```python
@app.middleware("http")
async def auth_mw(request: Request, call_next):
    # Canonicalise: strip trailing slash so /healthz/ == /healthz, and
    # use the parsed path (not the raw request line) so that any
    # ../ or %2E%2E sequences are already normalised by Starlette's
    # URL parser BEFORE we compare. This closes MF-7 (path-confusion).
    canon = request.url.path.rstrip("/") or "/"
    method = request.method.upper()
    if (canon, method) in PUBLIC_ENDPOINTS:
        return await call_next(request)
    if auth_token:
        hdr = request.headers.get("authorization", "")
        if not hdr.lower().startswith("bearer "):
            return _unauthorized()
        presented = hdr[len("bearer "):].strip()
        if presented != auth_token:
            return _unauthorized()
    return await call_next(request)
```

### Path-canonicalisation argument (MF-7 detail)

`request.url.path` is the parsed path component from Starlette's URL machinery, which uses `httptools` / `h11` to parse the raw HTTP request line. By the time we read `.path`, the value has been:
- URL-decoded once (so `%2F` → `/` and `%2E%2E` → `..`).
- Split from query string and fragment.

What it does NOT do automatically:
- Collapse `..` segments. A request line like `GET /openapi.json/../mcp HTTP/1.1` reaches us with `request.url.path == "/openapi.json/../mcp"`. The canonical comparison `canon == "/openapi.json"` returns False, so it falls through to auth — correct.
- Reject duplicate slashes. `/openapi.json//` becomes `/openapi.json//` then `rstrip("/")` produces `/openapi.json` — bypasses are still gated by **exact** match. We intentionally keep `rstrip("/")` (not regex normalize) because the only ambiguity we accept is "one trailing slash" — anything else is non-conforming and falls through.

**Verification** (AS-002 mitigation): The above behavioural claim is treated as an integration assumption, NOT axiom. The test suite includes a probe (`test_path_traversal_request_path_is_literal`) that fires `GET /openapi.json/../mcp` against a stub app and asserts `request.url.path == "/openapi.json/../mcp"` (literal, not normalised). If a future Starlette/uvicorn upgrade changes this, the probe fails loudly and forces re-design instead of silently regressing the gate. The probe also covers the `%2E%2E` URL-encoded variant where decoding-before-routing would matter.

### HEAD method (AS-004 mitigation)

FastAPI registers routes with the methods list passed in (`methods=["GET"]` for our handler). Starlette **does** auto-dispatch `HEAD` to `GET` handlers (returning headers but no body) — this is the documented Starlette behaviour. The auth middleware runs BEFORE Starlette's dispatch decision, so it sees `request.method == "HEAD"`. Therefore we MUST list `("/openapi.json", "HEAD")` explicitly in `PUBLIC_ENDPOINTS`. The test suite covers this: `test_openapi_json_head_method_public`.

### Why not "open `/openapi.json` only when no token is set"?

Tempting but wrong: the homelab proxy ALWAYS sets a token (HOMELAB_MCP_HTTP_TOKEN is required by the deployment). Conditional behaviour would mean "the spec is public unless you secured the deployment", which is the opposite of the desired contract. The spec endpoint is by-design discovery surface — auth status of execution is independent.

### Why not env-var-toggle (`HOMELAB_MCP_HTTP_PUBLIC_OPENAPI=true|false`)?

Considered. Rejected for now:
- Single deployment (this homelab cluster) — config knob with one valid value is dead weight.
- An env var that defaults to true gives a footgun: someone disables it, OpenWebUI breaks, no clear failure mode.
- If a future operator wants spec-private deployments, they can set `HOMELAB_MCP_HTTP_TOKEN` AND deploy behind a NetworkPolicy that blocks `/openapi.json` at the ingress layer (Traefik IPAllowList middleware). Application-layer toggle isn't the right place.

If a hard requirement appears later, the change is mechanical: add the env-var read in `create_app`, conditionally include the tuple in `PUBLIC_ENDPOINTS`. The design here doesn't preclude it.

## 4. Test Plan

`mcp/tests/test_http_app.py` will gain (per AC-9..AC-13 and AS-002/AS-004 mitigations):

- `test_openapi_json_public_when_token_set` — token set, no auth header → 200, body has paths.
- `test_openapi_json_trailing_slash_public` — same with `/openapi.json/`.
- `test_openapi_json_post_method_blocked` — POST /openapi.json → 405 (not 200, not 401).
- `test_openapi_json_path_traversal_blocked` — GET `/openapi.json/../mcp` and `/openapi.json/%2E%2E/mcp` → 401.
- `test_openapi_json_extra_segment_blocked` — GET `/openapi.json/anything` → 401.
- `test_openapi_json_degraded_still_serves_503` — degraded flag set → 503 returned even without auth.
- `test_openapi_json_head_method_public` — HEAD /openapi.json → 200 (Starlette HEAD-on-GET dispatch, AS-004).
- `test_path_traversal_request_path_is_literal` — integration probe verifying Starlette returns the literal path for `/openapi.json/../mcp` and the URL-encoded `%2E%2E` variant (AS-002 mitigation).
- Update existing `test_auth_token_blocks_unauthorized_request` (or equivalent) to verify it does NOT cover `/openapi.json`.

Negative tests to keep (regression guards for MF-1..6):
- `test_post_tool_without_auth_returns_401` — confirms tool execution still gated.
- `test_post_tool_with_wrong_token_returns_401`.
- `test_post_tool_with_correct_token_invokes_tool` — happy path.
- `test_mcp_path_without_auth_returns_401`.
- `test_mcp_path_with_correct_token_proxies` — confirms streamable mount still reachable.
- `test_unknown_path_with_no_auth_returns_401`.

## 5. File Inventory

Files modified by this SDD (paths relative to repo root `C:\src\_research\homelab_mcp`):

| File | Type | Change |
|------|------|--------|
| `mcp/src/homelab_mcp/http_app.py` | source | Add module-level `PUBLIC_ENDPOINTS` set; rewrite `auth_mw` to consult it; add SDD-traceability comment |
| `mcp/tests/test_http_app.py` | test | Add 8 new tests (see §4); update 1 existing test |
| `out/Rivet/sdd/public-openapi/contract.md` | sdd-artifact | (already written, step 2) |
| `out/Rivet/sdd/public-openapi/spec.md` | sdd-artifact | (already written, step 3) |
| `out/Rivet/sdd/public-openapi/design.md` | sdd-artifact | (this file) |
| `out/Rivet/sdd/public-openapi/as-findings.json` | sdd-artifact | (already written, step 4) |

Files created by this SDD:
- (none — feature lives entirely inside existing `http_app.py`)

Files NOT modified (out of scope, will be done in a separate cluster-rollout commit in the `homelab` repo):
- `apps/platform/mcp-proxy/deployment.yaml` (image tag bump after image is built+pushed)



## Rollout

1. Code change + tests merged in `homelab_mcp` repo (this SDD).
2. Image rebuilt, tagged `sha-<commit>`, pushed to `ghcr.io/dragoshont/homelab-mcp`.
3. `apps/platform/mcp-proxy/deployment.yaml` in `homelab` repo bumped to new tag.
4. Flux reconciles, pod rolls.
5. `curl -sf http://homelab-mcp-proxy.default.svc:8080/openapi.json` → 200.
6. **OpenWebUI re-discovery** (AS-003 mitigation): OpenWebUI's `tool_server` connection table caches the last-known tool list per connection. After `/openapi.json` becomes reachable, the cache may still hold the previous empty list. Force re-discovery either by:
   - **Pod restart** (cheap, recommended): `kubectl delete pod openwebui-open-webui-0` — on boot OpenWebUI re-reads `TOOL_SERVER_CONNECTIONS` env and re-issues GET /openapi.json against each connection.
   - OR an admin UI action: Settings → Tools → click `Verify` on the homelab connection.
7. Confirm AC-7 (DB query — `enable=True`, no `last_error`).
8. Smoke test AC-8 (chat → tool call → real data).
9. Smoke test AC-14 (latency loop, 100 requests).

## Rollback

Revert the bump in `apps/platform/mcp-proxy/deployment.yaml` to previous `sha-...` tag. Loss: OpenWebUI tool picker won't show homelab. Cluster otherwise unaffected.
