"""HTTP transport for homelab-mcp.

Wraps FastMCP's native Streamable HTTP app with FastAPI, adding:
- ``/healthz``  (always open; K8s liveness)
- ``/metrics``  (always open; Prometheus text format)
- ``/mcp/*``    (FastMCP Streamable HTTP, optional bearer-token auth)

This replaces the previous ``mcpo`` shim that wrapped stdio MCP as
HTTP. With FastMCP exposing a native Streamable HTTP factory we no
longer need a separate process.

Phase 1 of the architecture KB roadmap. Phase 2+ (REST mirror, public
exposure, full Prom instrumentation, tracing) is deliberately out of
scope here.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse


def _tool_count(mcp_obj) -> int:
    """Best-effort count of registered FastMCP tools.

    FastMCP 1.x exposes a sync ``_tool_manager.list_tools()`` and an
    *async* ``mcp.list_tools()`` coroutine. Health/metrics endpoints
    must stay sync-friendly, so we read through the tool manager.
    Falls back to private dicts if FastMCP renames things in a future
    bump (degradation is fail-safe: zero count -> /healthz still 200,
    but the ``homelab_mcp_up`` Prom gauge flips to 0 so alerting
    catches it).
    """
    tm = getattr(mcp_obj, "_tool_manager", None)
    if tm is not None:
        list_fn = getattr(tm, "list_tools", None)
        if callable(list_fn):
            try:
                return len(list_fn())
            except Exception:
                pass
        tm_tools = getattr(tm, "_tools", None)
        if tm_tools is not None:
            try:
                return len(tm_tools)
            except TypeError:
                pass
    for attr in ("_tools", "tools"):
        v = getattr(mcp_obj, attr, None)
        if v is None:
            continue
        try:
            return len(v)
        except TypeError:
            continue
    return 0


def create_app(
    *,
    auth_token: Optional[str] = None,
    mcp_obj=None,
) -> FastAPI:
    """Build the FastAPI application.

    Parameters
    ----------
    auth_token
        If non-empty, ``/mcp`` requests must carry
        ``Authorization: Bearer <auth_token>``. ``/healthz`` and
        ``/metrics`` are always open (K8s probes need them).
    mcp_obj
        FastMCP instance to mount. If None, imports
        ``homelab_mcp._runtime.mcp`` (the canonical singleton, same
        instance the stdio entry uses) AND triggers tool registration
        by importing the bundle entry. Passing a stub here is the
        intended test seam.

    Notes
    -----
    SDD ``fastapi-phase1`` rules in force:

    - Phase 1: ``docs_url=None``. Phase 2 will enable Swagger.
    - Bearer-token compare is plain ``!=`` (not constant-time). For a
      homelab single-user deployment behind SSH or Cloudflare Access
      this is sufficient. Phase 3 will switch to ``hmac.compare_digest``
      before any public exposure.
    """
    if mcp_obj is None:
        # Import lazily so test harnesses can pass a stub.
        from homelab_mcp._runtime import mcp as _mcp
        # Side-effect import: registers all bundle tools onto the
        # singleton. Done at app construction (BEFORE uvicorn starts
        # serving) so the first /healthz request is fast.
        from homelab_mcp import server  # noqa: F401
        mcp_obj = _mcp

    app = FastAPI(
        title="homelab-mcp",
        version="phase1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def auth_mw(request: Request, call_next):
        # Probes always open so K8s liveness/readiness never depend on
        # secret rotation. Compare with rstrip('/') so trailing-slash
        # variants (some Ingress controllers / probes append one) are
        # not accidentally auth-blocked.
        if request.url.path.rstrip("/") in ("/healthz", "/metrics"):
            return await call_next(request)
        if auth_token:
            hdr = request.headers.get("authorization", "")
            # RFC 7235/6750: the scheme token is case-insensitive.
            # Accept "Bearer", "bearer", "BEARER", etc.
            if not hdr.lower().startswith("bearer "):
                return JSONResponse(
                    {"error": "unauthorized"}, status_code=401
                )
            presented = hdr[len("bearer ") :].strip()
            if presented != auth_token:
                return JSONResponse(
                    {"error": "unauthorized"}, status_code=401
                )
        return await call_next(request)

    @app.get("/healthz")
    async def healthz():
        n = _tool_count(mcp_obj)
        body = {
            "status": "ok" if n > 0 else "degraded",
            "tools": n,
            "name": getattr(mcp_obj, "name", "homelab"),
        }
        # Zero tools means tool registration failed (e.g. import-time
        # error in a tools/* module). K8s liveness reads only the HTTP
        # status, so we MUST return non-200 to trigger a restart.
        if n == 0:
            return JSONResponse(body, status_code=503)
        return body

    @app.get("/metrics")
    async def metrics():
        n = _tool_count(mcp_obj)
        body = (
            "# HELP homelab_mcp_up 1 if the server has loaded tools.\n"
            "# TYPE homelab_mcp_up gauge\n"
            f"homelab_mcp_up {1 if n > 0 else 0}\n"
            "# HELP homelab_mcp_tools_total Number of registered FastMCP tools.\n"
            "# TYPE homelab_mcp_tools_total gauge\n"
            f"homelab_mcp_tools_total {n}\n"
        )
        return PlainTextResponse(
            body, media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    # Trailing-slash variants for probes. We mount the FastMCP
    # Streamable HTTP app at "/" below; that mount otherwise swallows
    # "/healthz/" and "/metrics/" and 404s before the explicit routes
    # above can resolve. Some Ingress controllers / curl-with-redirect
    # / probe configurations append a trailing slash, so handle them
    # explicitly. (verify ADV-007, R3)
    app.add_api_route("/healthz/", healthz, methods=["GET"])
    app.add_api_route("/metrics/", metrics, methods=["GET"])

    # Mount FastMCP's native Streamable HTTP transport.
    #
    # FastMCP's streamable_http_app() returns a Starlette app whose
    # canonical endpoint is at the path configured by
    # ``mcp.settings.streamable_http_path`` (default: ``/mcp``). We
    # mount it at root so the public URL is exactly that path -
    # mounting at ``/mcp`` would result in ``/mcp/mcp``, which the
    # MCP spec does not define and which standard clients won't hit.
    streamable = mcp_obj.streamable_http_app()
    app.mount("/", streamable)

    return app


def run_uvicorn() -> None:
    """Console-script entrypoint (``homelab-mcp-http``).

    Reads ``HOMELAB_MCP_HTTP_HOST`` (default ``0.0.0.0``),
    ``HOMELAB_MCP_HTTP_PORT`` (default ``8080``), and optional
    ``HOMELAB_MCP_HTTP_TOKEN`` (no auth if unset).
    """
    import uvicorn

    host = os.environ.get("HOMELAB_MCP_HTTP_HOST", "0.0.0.0")
    port_raw = os.environ.get("HOMELAB_MCP_HTTP_PORT", "8080")
    try:
        port = int(port_raw)
    except ValueError:
        # Spec A3: stderr message MUST mention the env var name.
        print(
            f"HOMELAB_MCP_HTTP_PORT is not a valid integer: {port_raw!r}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    auth_token = os.environ.get("HOMELAB_MCP_HTTP_TOKEN") or None
    if auth_token is not None:
        # Strip whitespace/newlines that commonly creep in from
        # secret-file sourcing (e.g. `kubectl get secret ... -o
        # jsonpath=... | base64 -d` often emits a trailing newline).
        # Without this strip, the env-side has '\n' but the presented
        # header value (already stripped in auth_mw) doesn't, so all
        # clients get a permanent 401 with no diagnostic.
        auth_token = auth_token.strip() or None
    app = create_app(auth_token=auth_token)
    uvicorn.run(app, host=host, port=port, log_level="info")
