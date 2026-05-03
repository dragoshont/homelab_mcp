"""HTTP transport for homelab-mcp.

Wraps FastMCP's native Streamable HTTP app with FastAPI, adding:

- ``/healthz``       (always open; K8s liveness)
- ``/metrics``       (always open; Prometheus text format)
- ``/openapi.json``  (Phase 2: tools-only OpenAPI 3 doc, mcpo-compat)
- ``POST /<tool>``   (Phase 2: per-tool route mirroring FastMCP registry)
- ``/mcp/*``         (FastMCP Streamable HTTP, optional bearer-token auth)

Phase 1 (homelab_mcp PR #15) replaced ``mcpo`` with the native FastAPI
app. Phase 2 reintroduces the OpenAPI tool-server mirror that mcpo used
to expose, so OpenWebUI's existing ``TOOL_SERVER_CONNECTIONS`` config
keeps working unchanged.

Phase 3+ (public exposure via mcp.hont.ro, full Prom instrumentation,
tracing, hmac-compare auth) is out of scope here.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import sys
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError

logger = logging.getLogger(__name__)

# Phase 2: route registration guards.
#
# Tool names become URL path segments. We refuse to register routes for
# names with characters that would either (a) confuse routing (slash,
# wildcard chars) or (b) collide with a reserved internal path.
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_-]*$")
_RESERVED_PATHS = frozenset(
    {"/healthz", "/metrics", "/mcp", "/openapi.json"}
)


def _tool_count(mcp_obj) -> int:
    """Best-effort count of registered FastMCP tools.

    Returns -1 (sentinel: tool manager errored) when the primary
    accessor raises; the caller treats that as degraded. Returns 0
    when the registry is reachable but empty. Returns >0 on success.
    """
    tm = getattr(mcp_obj, "_tool_manager", None)
    if tm is not None:
        list_fn = getattr(tm, "list_tools", None)
        if callable(list_fn):
            try:
                return len(list_fn())
            except Exception:
                # Primary accessor errored. Do NOT fall through to the
                # private dict (would mask a broken tool manager
                # behind a stale count). Signal degraded.
                return -1
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
        If non-empty, ``/mcp``, ``/openapi.json`` and ``POST /<tool>``
        require ``Authorization: Bearer <auth_token>``. ``/healthz``
        and ``/metrics`` are always open.
    mcp_obj
        FastMCP instance to mount. If None, imports
        ``homelab_mcp._runtime.mcp`` (the canonical singleton, same
        instance the stdio entry uses) AND triggers tool registration
        by importing the bundle entry. Passing a stub here is the
        intended test seam.
    """
    if mcp_obj is None:
        from homelab_mcp._runtime import mcp as _mcp
        # Side-effect import: registers all bundle tools onto the
        # singleton.
        from homelab_mcp import server  # noqa: F401
        mcp_obj = _mcp

    # Defense-in-depth (verify ADV-004): normalize the configured token
    # at the create_app boundary too, not only in run_uvicorn().
    if auth_token is not None:
        auth_token = auth_token.strip() or None

    app = FastAPI(
        title="homelab-mcp",
        version="phase2",
        docs_url=None,
        redoc_url=None,
        # Disable FastAPI's auto-generated /openapi.json — we serve
        # our own (tools-only) below.
        openapi_url=None,
    )

    def _unauthorized() -> JSONResponse:
        # RFC 6750 §3: 401 MUST include a WWW-Authenticate challenge.
        return JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="homelab-mcp"'},
        )

    @app.middleware("http")
    async def auth_mw(request: Request, call_next):
        # Probes always open so K8s liveness/readiness never depend on
        # secret rotation. rstrip("/") to also accept trailing-slash
        # variants used by some Ingress controllers.
        if request.url.path.rstrip("/") in ("/healthz", "/metrics"):
            return await call_next(request)
        if auth_token:
            hdr = request.headers.get("authorization", "")
            # RFC 7235/6750: scheme token is case-insensitive.
            if not hdr.lower().startswith("bearer "):
                return _unauthorized()
            presented = hdr[len("bearer "):].strip()
            if presented != auth_token:
                return _unauthorized()
        return await call_next(request)

    @app.get("/healthz")
    async def healthz():
        n = _tool_count(mcp_obj)
        if n < 0:
            body = {
                "status": "degraded",
                "tools": 0,
                "name": getattr(mcp_obj, "name", "homelab"),
                "reason": "tool_manager_unreachable",
            }
            return JSONResponse(body, status_code=503)
        body = {
            "status": "ok" if n > 0 else "degraded",
            "tools": n,
            "name": getattr(mcp_obj, "name", "homelab"),
        }
        if n == 0:
            return JSONResponse(body, status_code=503)
        return body

    @app.get("/metrics")
    async def metrics():
        n = _tool_count(mcp_obj)
        safe_n = max(n, 0)
        body = (
            "# HELP homelab_mcp_up 1 if the server has loaded tools.\n"
            "# TYPE homelab_mcp_up gauge\n"
            f"homelab_mcp_up {1 if safe_n > 0 else 0}\n"
            "# HELP homelab_mcp_tools_total Number of registered FastMCP tools.\n"
            "# TYPE homelab_mcp_tools_total gauge\n"
            f"homelab_mcp_tools_total {safe_n}\n"
        )
        return PlainTextResponse(
            body, media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    # Trailing-slash probe variants (Phase 1 ADV-007 fix).
    app.add_api_route("/healthz/", healthz, methods=["GET"])
    app.add_api_route("/metrics/", metrics, methods=["GET"])

    # Phase 2: register the OpenAPI tool-server mirror BEFORE the
    # catch-all FastMCP mount. Order matters: FastAPI dispatches in
    # registration order; routes registered after the mount would
    # never be reached.
    _register_openapi_mirror(app, mcp_obj)

    # Mount FastMCP's native Streamable HTTP transport at root.
    streamable = mcp_obj.streamable_http_app()
    app.mount("/", streamable)

    return app


# ---------------------------------------------------------------------------
# Phase 2: OpenAPI tool-server mirror
# ---------------------------------------------------------------------------


def _inline_defs(schema: dict) -> dict:
    """Resolve ``#/$defs/*`` ``$ref`` entries by inlining.

    FastMCP serialises tool parameter schemas with a top-level
    ``$defs`` registry referenced by ``$ref`` from nested properties.
    Splatting that schema into a per-path requestBody loses the
    registry; OpenWebUI's importer can't resolve the refs and silently
    drops the tool (verify F-006).

    Inlines refs by deep-walk; preserves a single self-reference by
    leaving the inner ``$ref`` intact when re-entering the same key.
    """
    defs = schema.get("$defs") or schema.get("definitions") or {}
    if not defs:
        return schema

    seen: set[str] = set()

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith(
                ("#/$defs/", "#/definitions/")
            ):
                key = ref.split("/")[-1]
                if key in seen:
                    return node
                target = defs.get(key)
                if target is not None:
                    seen.add(key)
                    inlined = walk(copy.deepcopy(target))
                    seen.discard(key)
                    return inlined
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out = walk(copy.deepcopy(schema))
    if isinstance(out, dict):
        out.pop("$defs", None)
        out.pop("definitions", None)
    return out


def _unwrap_call_tool_result(result: Any) -> Any:
    """Convert a FastMCP tool-run result to a JSON-friendly value.

    FastMCP versions vary in shape:

    - Newer FastMCP returns ``CallToolResult`` (object with
      ``structuredContent`` and ``content`` attributes).
    - 1.x ``Tool.run(..., convert_result=True)`` returns
      ``list[TextContent | ImageContent | ...]`` directly.
    - Some tool functions return dicts/strings/numbers verbatim
      (when called without ``convert_result``).

    Preference order:
    1. ``result.structuredContent`` if present.
    2. Iterate ``result.content`` (or ``result`` itself if list) and
       concatenate ``.text`` fields; try ``json.loads`` first,
       fall back to ``{"text": ...}``.
    3. ``str(result)`` envelope as last resort.
    """
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    # Either result.content (CallToolResult) or result itself (list).
    content = getattr(result, "content", None)
    if content is None and isinstance(result, list):
        content = result
    if isinstance(content, list) and content:
        texts = [
            getattr(item, "text", None)
            for item in content
            if getattr(item, "text", None) is not None
        ]
        if texts:
            joined = "".join(texts)
            try:
                return json.loads(joined)
            except (json.JSONDecodeError, ValueError):
                return {"text": joined}
    return {"text": str(result)}


def _make_tool_handler(tool):
    """Build an async FastAPI handler that invokes ``tool``.

    Uses ``tool.run(args, convert_result=True)`` (NOT
    ``tool.fn(**args)``) so FastMCP's pydantic argument validation
    surfaces clean 400s instead of 500s (verify F-001/F-002).
    """

    async def handler(request: Request):
        raw = await request.body()
        if raw:
            try:
                payload = await request.json()
            except json.JSONDecodeError:
                return JSONResponse(
                    {"error": "invalid JSON body"}, status_code=400
                )
        else:
            payload = {}
        if not isinstance(payload, dict):
            return JSONResponse(
                {"error": "body must be a JSON object"}, status_code=400
            )
        try:
            result = await tool.run(payload, convert_result=True)
        except ValidationError as exc:
            return JSONResponse(
                {"error": f"ValidationError: {exc}"}, status_code=400
            )
        except TypeError as exc:
            return JSONResponse(
                {"error": f"TypeError: {exc}"}, status_code=400
            )
        except Exception as exc:
            # FastMCP wraps argument-validation failures in
            # ToolError. Detect via the cause chain so we still
            # return 400 for bad-input cases (verify F-001).
            cause = getattr(exc, "__cause__", None)
            if isinstance(cause, ValidationError) or isinstance(
                cause, TypeError
            ):
                return JSONResponse(
                    {"error": f"{type(cause).__name__}: {cause}"},
                    status_code=400,
                )
            # Last-resort envelope. Traceback intentionally NOT in body.
            logger.exception("tool %r raised", tool.name)
            return JSONResponse(
                {"error": f"{type(exc).__name__}: {exc}"},
                status_code=500,
            )
        unwrapped = _unwrap_call_tool_result(result)
        try:
            return JSONResponse(jsonable_encoder(unwrapped))
        except Exception as exc:
            logger.exception(
                "unserializable result from tool %r", tool.name
            )
            return JSONResponse(
                {
                    "error": (
                        f"unserializable result: "
                        f"{type(exc).__name__}: {exc}"
                    )
                },
                status_code=500,
            )

    handler.__name__ = f"tool_{tool.name}"
    return handler


def _build_openapi_doc(
    tools,
    *,
    server_title: str = "homelab-mcp",
    server_version: str = "phase2",
) -> dict:
    """Build a tools-only OpenAPI 3.1 document (mcpo-compatible)."""
    paths: dict = {}
    for tool in tools:
        if not _TOOL_NAME_RE.match(tool.name):
            continue
        if f"/{tool.name}" in _RESERVED_PATHS:
            continue
        params = _inline_defs(
            tool.parameters or {"type": "object", "properties": {}}
        )
        summary = (tool.description or tool.name).split("\n")[0][:200]
        paths[f"/{tool.name}"] = {
            "post": {
                "operationId": tool.name,
                "summary": summary,
                "description": tool.description or "",
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {"schema": params},
                    },
                },
                "responses": {
                    "200": {
                        "description": "Tool result",
                        "content": {
                            "application/json": {"schema": {}},
                        },
                    },
                    "400": {"description": "Bad request"},
                    "500": {"description": "Tool error"},
                },
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": server_title,
            "version": server_version,
            "description": (
                "Homelab MCP tools as an OpenAPI tool-server "
                "(mcpo-compatible mirror of the FastMCP registry)."
            ),
        },
        "paths": paths,
    }


def _register_openapi_mirror(app: FastAPI, mcp_obj) -> None:
    """Register ``POST /<tool>`` + ``GET /openapi.json`` on ``app``.

    Must be called BEFORE ``app.mount("/", streamable)`` so per-tool
    exact-path routes take precedence over the catch-all FastMCP
    mount.
    """
    tm = getattr(mcp_obj, "_tool_manager", None)
    registered: list = []
    if tm is None or not callable(getattr(tm, "list_tools", None)):
        logger.warning(
            "tool manager unavailable; OpenAPI mirror skipped"
        )
    else:
        try:
            tools = list(tm.list_tools())
        except Exception:
            logger.exception(
                "list_tools failed; OpenAPI mirror skipped"
            )
            tools = []
        for tool in tools:
            name = getattr(tool, "name", None)
            if not isinstance(name, str) or not _TOOL_NAME_RE.match(name):
                logger.warning("skip tool %r: invalid HTTP name", name)
                continue
            if f"/{name}" in _RESERVED_PATHS:
                logger.warning(
                    "skip tool %r: collides with reserved path", name
                )
                continue
            app.add_api_route(
                f"/{name}",
                _make_tool_handler(tool),
                methods=["POST"],
                name=f"tool_{name}",
                include_in_schema=False,
            )
            registered.append(tool)

    # Cache the doc on app.state so tests can override it.
    app.state.openapi_doc = _build_openapi_doc(registered)

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_json():
        return app.state.openapi_doc


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
        print(
            f"HOMELAB_MCP_HTTP_PORT is not a valid integer: {port_raw!r}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if not (1 <= port <= 65535):
        print(
            f"HOMELAB_MCP_HTTP_PORT is out of range (1..65535): {port}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    raw_token = os.environ.get("HOMELAB_MCP_HTTP_TOKEN")
    if raw_token is None or raw_token == "":
        auth_token = None
    else:
        auth_token = raw_token.strip()
        if not auth_token:
            print(
                "HOMELAB_MCP_HTTP_TOKEN is set but contains only whitespace; "
                "refusing to start with auth disabled. Unset the variable to "
                "run without auth, or set it to a non-blank token.",
                file=sys.stderr,
            )
            raise SystemExit(2)
    app = create_app(auth_token=auth_token)
    uvicorn.run(app, host=host, port=port, log_level="info")
