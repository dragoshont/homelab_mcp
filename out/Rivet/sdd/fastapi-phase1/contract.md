# Verify Contract — fastapi-phase1

## Background

`homelab-mcp` today exposes its 133 tools via a `FastMCP` stdio server
(`homelab_mcp.server:main`). The container image wraps it with `mcpo`
(`mcpo --port 8080 --host 0.0.0.0 -- homelab-mcp`) to get an HTTP surface
that the in-cluster `mcp-proxy` can talk to. The host (`home.hont.ro`)
overrides the entrypoint with `--entrypoint homelab-mcp` to talk plain
stdio over SSH.

Phase 1 replaces the `mcpo` shim with a native FastAPI app that mounts
the FastMCP **Streamable HTTP** transport, plus health/metrics endpoints
and a bearer-token middleware. **Same image, same port (8080), three
surfaces from one process**:

- stdio MCP — `python -m homelab_mcp` (unchanged; SSH wrapper keeps working)
- Streamable HTTP MCP — `POST /mcp` (replaces mcpo's OpenAPI bridge)
- FastAPI ops — `GET /healthz`, `GET /metrics` (Prom format)

Out of scope for Phase 1: REST `/v1/*` mirror of every tool, public
exposure via `mcp.hont.ro`, OpenWebUI cutover. Those are Phase 2-4 in
the architecture KB §11.

## MUST PASS

1. **MP-1** New console script `homelab-mcp-http` exists in `pyproject.toml` and starts a uvicorn server bound to `0.0.0.0:8080` (port + host configurable via env `HOMELAB_MCP_HTTP_HOST`, `HOMELAB_MCP_HTTP_PORT`).
2. **MP-2** `GET /healthz` on the running server returns 200 with JSON body containing `{"status":"ok","tools":<int>}` where `tools` is the count of registered FastMCP tools (>0).
3. **MP-3** `POST /mcp` with a JSON-RPC `initialize` request returns a valid MCP `serverInfo` payload identifying as `homelab` (name preserved from current stdio server).
4. **MP-4** When env `HOMELAB_MCP_HTTP_TOKEN` is set, requests to `/mcp` without `Authorization: Bearer <token>` return 401 carrying a `WWW-Authenticate: Bearer realm="homelab-mcp"` header (RFC 6750 §3). With the correct token, they succeed. **`/healthz` and `/metrics` are ALWAYS open** regardless of token configuration so K8s liveness/readiness probes never depend on secret rotation.
5. **MP-5** When `HOMELAB_MCP_HTTP_TOKEN` is unset/empty, no auth is enforced on `/mcp` (homelab default — auth is added at the network edge by Cloudflare Access in Phase 3). When the env var is set but contains only whitespace, `homelab-mcp-http` exits 2 at startup (fail-closed; misconfigured-secret guard).
6. **MP-6** Existing `homelab-mcp` stdio entrypoint is unchanged; SSH-stdio handshake on the host still returns the same `serverInfo`.
7. **MP-7** Container image `ENTRYPOINT` is replaced from `mcpo --port 8080 -- homelab-mcp` to `homelab-mcp-http`. The container exits with code != 0 if uvicorn fails to bind.
8. **MP-8** `mcpo` is removed from the image (no longer in `pip install` line of the Dockerfile).
9. **MP-9** All existing tests (`mcp/tests/`) still pass.

## MUST FAIL

1. **MF-1** Starting `homelab-mcp-http` with `HOMELAB_MCP_HTTP_PORT=invalid` exits with non-zero exit code and a clear error mentioning the port var.
2. **MF-2** Sending an unauthenticated request when `HOMELAB_MCP_HTTP_TOKEN` is set returns 401, NOT 500 or a partial MCP handshake.
3. **MF-3** A POST to `/mcp` with a malformed JSON-RPC body returns 400 / a JSON-RPC error, not 500.
4. **MF-4** An exception inside a tool implementation does NOT leak a stack trace in the HTTP response (only the JSON-RPC error envelope).

## Integration Points

- **Console script** in `mcp/pyproject.toml` `[project.scripts]`: new `homelab-mcp-http` line pointing at `homelab_mcp.http_app:run_uvicorn`.
- **New module** `mcp/src/homelab_mcp/http_app.py`: defines `create_app(*, auth_token: str | None) -> FastAPI` and `run_uvicorn()` entrypoint.
- **Imports** the existing `mcp` instance from `homelab_mcp._runtime` (same singleton stdio uses).
- **Dockerfile** ENTRYPOINT changed; `mcpo` removed from `pip install`.
- **No changes to** `homelab_mcp/server.py`, `app.py`, any `tools/*.py`, or any test.
- **Cluster chart** (in separate `homelab` repo) requires a `tag:` bump in a follow-up PR — explicitly OUT OF SCOPE here.

## Out of Scope

- REST `/v1/*` endpoints (Phase 2)
- TLS termination inside the container (terminated at cloudflared/ingress)
- Rate limiting (Phase 3)
- OpenTelemetry tracing (Phase 3)
- Prometheus metrics beyond a stub `/metrics` returning 200 OK with a couple of counters; full instrumentation is Phase 3

## User-Capability Sentence (RC-15)

After this SDD ships, the user can:
1. `curl http://homelab-mcp.svc:8080/healthz` from inside the cluster and see tool count.
2. Point an MCP-compatible client at `http://homelab-mcp.svc:8080/mcp` (Streamable HTTP) instead of through mcpo, and exercise any tool.
3. SSH into `home.hont.ro` and run `homelab-mcp-wrapper` — it still works because the stdio entry is untouched.
