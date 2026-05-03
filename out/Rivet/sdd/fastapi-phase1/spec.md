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

A1. New module `mcp/src/homelab_mcp/http_app.py` provides
    `create_app(*, auth_token: str | None = None, mcp_factory=None) -> FastAPI`.

A2. `pyproject.toml` adds console script `homelab-mcp-http =
    "homelab_mcp.http_app:run_uvicorn"`.

A3. `homelab-mcp-http` launches uvicorn bound to
    `${HOMELAB_MCP_HTTP_HOST:-0.0.0.0}:${HOMELAB_MCP_HTTP_PORT:-8080}`.
    Invalid port → exit 2 (uvicorn convention) with a stderr line
    containing `HOMELAB_MCP_HTTP_PORT`.

A4. `GET /healthz` returns 200 with body
    `{"status":"ok","tools":<int>,"name":"homelab"}` where `tools` is
    `len(mcp._tools)` (or equivalent FastMCP internal); always > 0.

A5. `POST /mcp` (Streamable HTTP) responds to `initialize` with the
    same `serverInfo.name == "homelab"` and `serverInfo.version` as
    `homelab-mcp` stdio does.

A6. With `HOMELAB_MCP_HTTP_TOKEN=secret` env var:
    - `GET /healthz` without `Authorization` header → **200** (health
      MUST stay open for K8s probes; auth never blocks liveness).
    - `POST /mcp` without `Authorization` → 401, body
      `{"error":"unauthorized"}`.
    - With `Authorization: Bearer secret` → 200/200, normal flow.
    *(MUST FAIL #4: tool exception → JSON-RPC error envelope, not
    Python traceback in the response body.)*

A7. With `HOMELAB_MCP_HTTP_TOKEN` unset/empty: no auth check on `/mcp`
    (`/healthz` is always open).

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

A9. Dockerfile change:
    - `pip install --no-cache-dir . mcpo` → `pip install --no-cache-dir .`.
    - `ENTRYPOINT ["mcpo", "--port", "8080", "--host", "0.0.0.0", "--", "homelab-mcp"]`
      → `ENTRYPOINT ["homelab-mcp-http"]`.
    - `EXPOSE 8080` unchanged.

A10. `homelab-mcp` stdio entrypoint behaviour unchanged. `homelab-mcp
     --help` (or running with stdin closed) behaves the same as before.

A11. New unit test `tests/test_http_app.py`:
    - test_healthz_returns_ok_and_tool_count (uses `httpx.AsyncClient` against `create_app()`)
    - test_mcp_initialize_returns_homelab (POST to `/mcp` with init payload)
    - test_unauthenticated_mcp_post_is_401_when_token_set
    - test_authenticated_mcp_post_is_200_when_token_set
    - test_no_token_means_no_auth
    - test_metrics_format_is_prom

A12. All existing tests still pass.

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
