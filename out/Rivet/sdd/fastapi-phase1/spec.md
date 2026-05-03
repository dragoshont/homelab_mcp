# Spec — fastapi-phase1

## Problem

`homelab-mcp` ships an HTTP surface today via `mcpo` (a generic
stdio↔HTTP shim baked into the container image). `mcpo` works but:

1. **Generic — no homelab-specific concerns.** Bearer auth, health
   probes, and metrics are bolted on or absent.
2. **Two processes per pod.** mcpo launches `homelab-mcp` as a subprocess
   and trades JSON-RPC over stdio. Doubles RAM, halves observability.
3. **Auto-generated OpenAPI** that the mcp-proxy already discards in
   favour of the MCP Streamable HTTP transport.
4. **Unmaintained dependency** for our use-case — every behaviour we want
   (auth middleware, health checks, structured logs) means writing
   wrappers around mcpo, not extending it.
5. **Foreign to FastMCP.** FastMCP itself ships a `streamable_http_app()`
   factory returning a Starlette app — already MCP-spec-compliant. We
   are paying for mcpo to do something the upstream library does
   natively.

## Goals

1. Replace `mcpo` with a small in-process FastAPI app that mounts
   FastMCP's native Streamable HTTP transport.
2. Add operational endpoints (`/healthz`, `/metrics`) that exist in ONE
   process where the tools also run — accurate liveness signal.
3. Add optional bearer-token auth controlled by env var
   (`HOMELAB_MCP_HTTP_TOKEN`); off by default, on when set, no
   per-deployment middleware shuffling.
4. Preserve the stdio entrypoint exactly — host SSH wrapper untouched.
5. Slim the image: drop `mcpo` from `pip install`.

## Non-Goals

- REST `/v1/*` routes mirroring tools (Phase 2).
- Public exposure via `mcp.hont.ro` / Cloudflare Access (Phase 3).
- OpenWebUI cutover (Phase 4 — chart-side change in `homelab` repo).
- Full Prom instrumentation (Phase 3) — Phase 1 ships a stub `/metrics`
  with a `homelab_mcp_tools_total` gauge and an `up` counter.
- TLS termination inside the container (terminated at ingress).
- Rate limiting (Phase 3).
- Modifying any `tools/*.py` or any test.

## Acceptance Criteria

> **Note:** Updated post-shipment (R5/R6) to reflect the actually-shipped
> behavior. Earlier drafts referenced an `mcp_factory` keyword and a
> simpler healthz contract; the implementation diverged for good
> reasons (degraded 503 semantics, fail-closed token, tool-manager
> error sentinel, port range validation). This section is the
> reviewer-facing contract.

A1. New module `mcp/src/homelab_mcp/http_app.py` provides
    `create_app(*, auth_token: str | None = None, mcp_obj=None) -> FastAPI`.
    `mcp_obj` is the test seam (a stub FastMCP-shaped object); when
    `None`, the canonical `homelab_mcp._runtime.mcp` singleton is
    imported lazily after the bundle entry-point side-effect-imports
    all 133 tools.

A2. `pyproject.toml` adds console script `homelab-mcp-http =
    "homelab_mcp.http_app:run_uvicorn"`.

A3. `homelab-mcp-http` launches uvicorn bound to
    `${HOMELAB_MCP_HTTP_HOST:-0.0.0.0}:${HOMELAB_MCP_HTTP_PORT:-8080}`.
    - Non-integer port → exit 2 with a stderr line containing
      `HOMELAB_MCP_HTTP_PORT`.
    - Out-of-range port (not in `1..65535`) → exit 2 with a stderr
      line citing the value (R4 hardening).

A4. `GET /healthz` returns:
    - **200** with body `{"status":"ok","tools":<N>,"name":"homelab"}`
      when tools are registered (`N > 0`).
    - **503** with body `{"status":"degraded","tools":0,...}` when no
      tools are registered (silent registration failure must restart
      the pod; R1 hardening).
    - **503** with body `{"status":"degraded","tools":0,"reason":
      "tool_manager_unreachable",...}` when the FastMCP tool manager
      raises (R5 hardening — primary accessor must not be masked by a
      stale `_tools` dict fallback).

A5. `POST /mcp` (Streamable HTTP) responds to a JSON-RPC `initialize`
    request with the same `serverInfo.name == "homelab"` and
    `serverInfo.version` as `homelab-mcp` stdio does. The endpoint is
    canonically reachable at `/mcp` (R2 fix — earlier drafts mounted
    the streamable app at `/mcp`, producing `/mcp/mcp`; the streamable
    sub-app is now mounted at `/`).

A6. With `HOMELAB_MCP_HTTP_TOKEN=<token>` env var (after `strip()`):
    - `GET /healthz` and `GET /metrics` (and their trailing-slash
      variants `/healthz/`, `/metrics/`) without `Authorization` →
      probe behavior continues unchanged. Probes are ALWAYS open
      regardless of auth config so K8s liveness/readiness never
      depend on secret rotation.
    - `POST /mcp` without `Authorization` → 401 with body
      `{"error":"unauthorized"}` AND header `WWW-Authenticate: Bearer
      realm="homelab-mcp"` (RFC 6750 §3, post-review hardening).
    - `POST /mcp` with `Authorization: Bearer <token>` → request
      passes middleware; status mirrors what the streamable app
      returns. Scheme token compare is case-insensitive (RFC 7235;
      R1 hardening).

A7. With `HOMELAB_MCP_HTTP_TOKEN` unset or empty: no auth check on
    `/mcp`. With the var set but containing only whitespace,
    `run_uvicorn` exits 2 (fail-closed against misconfigured secrets;
    R4 hardening). `create_app(auth_token="   ")` programmatically
    downgrades to no-auth — library callers may legitimately want
    no-auth via this path.

A8. `GET /metrics` returns 200 with content-type
    `text/plain; version=0.0.4` and at minimum:
    ```
    # HELP homelab_mcp_up 1 if the server has loaded tools.
    # TYPE homelab_mcp_up gauge
    homelab_mcp_up 1
    # HELP homelab_mcp_tools_total Number of registered FastMCP tools.
    # TYPE homelab_mcp_tools_total gauge
    homelab_mcp_tools_total <N>
    ```
    On tool-manager error, both gauges clamp to 0 (R5).

A9. Dockerfile change:
    - `pip install --no-cache-dir . mcpo` → `pip install --no-cache-dir .`.
    - `ENTRYPOINT ["mcpo", "--port", "8080", "--host", "0.0.0.0", "--", "homelab-mcp"]`
      → `ENTRYPOINT ["homelab-mcp-http"]`.
    - `EXPOSE 8080` unchanged.

A10. `homelab-mcp` stdio entrypoint behaviour unchanged. The host SSH
     wrapper continues to work via `docker run --entrypoint
     homelab-mcp ...`.

A11. New unit test `tests/test_http_app.py` ships **18 tests** covering:
     - healthz happy path (200 + tool count) and zero-tools/manager-error 503 paths
     - metrics prom format on canonical and trailing-slash paths
     - auth: 401 without token + WWW-Authenticate header; bearer-scheme
       case-insensitivity; pass-through for valid token; no-auth path; healthz
       open even with token set; trailing-slash paths still open
     - run_uvicorn: token whitespace stripping, whitespace-only
       fail-closed, port out-of-range parametrised cases
     - create_app token whitespace normalization
     - JSON-RPC initialize integration (proves /mcp is correctly mounted)

A12. All pre-existing tests (171) still collect and pass. Total suite:
     189 passing.

## Risks

- **R1 — FastMCP API may not expose Streamable HTTP factory** by the
  name we expect. Mitigation: probe `mcp.server.fastmcp` during
  implementation; fall back to mounting FastMCP's `_session_manager` /
  `app` directly. Spike before locking design.
- **R2 — Tool count introspection** (`mcp._tools` private attribute).
  Mitigation: prefer a public method if any (`list_tools()`, registry
  iteration); accept reading a private attr for Phase 1 (homelab is
  single-vendor, monolith).
- **R3 — Health probe races startup.** Tools register on import side
  effect. Solution: `healthz` reads after import is complete; uvicorn
  starts only after `create_app()` returns, by which time imports have
  resolved.
- **R4 — Image size** could shrink (mcpo gone) but uvicorn is added.
  Net delta likely small (uvicorn ~3 MB); acceptable.
- **R5 — Entrypoint interplay**: container ENTRYPOINT changes from
  `mcpo` to `homelab-mcp-http`. The host-side wrapper uses
  `--entrypoint homelab-mcp` to override → keep working unconditionally.

## Reference

- Architecture KB §11 / `home-architecture.md` §3.5 (mcp-proxy / surfaces)
- MCP spec 2025-06-18 transports section
- FastMCP `streamable_http_app()` docs in `mcp[cli]>=1.0`
