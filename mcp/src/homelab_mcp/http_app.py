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
# Always-reserved paths (open even when streamable path is moved).
_BASE_RESERVED_PATHS = frozenset(
    {"/healthz", "/metrics", "/openapi.json"}
)

# SDD: public-openapi (out/Rivet/sdd/public-openapi).
#
# Public endpoints — bypass the bearer middleware. Listed as
# (canonical_path, METHOD) tuples so a future addition cannot
# accidentally make POST/PUT/DELETE on `/openapi.json` public (MF-8).
#
# Why each path is here:
#   - /healthz, /metrics: K8s probes / scrape jobs that MUST work
#     without secret rotation (Phase 1 contract).
#   - /openapi.json: tool-server discovery endpoint. OpenWebUI's
#     TOOL_SERVER_CONNECTIONS importer issues an unauthenticated GET
#     (auth_type: none in helmrelease.yaml). The doc only describes
#     the API surface (tool names + JSON schemas) — no data, no
#     mutations, no credentials. Tool **execution** (POST /<tool>)
#     and the MCP transport (/mcp/*) remain bearer-gated. This is
#     the same boundary mcpo (the previous tool-server) drew.
#
# HEAD entries are explicit: Starlette auto-dispatches HEAD onto GET
# routes, but the auth middleware sees method == "HEAD" before that
# dispatch, so it must be in this set or HEAD probes get 401.
PUBLIC_ENDPOINTS: frozenset[tuple[str, str]] = frozenset({
    ("/healthz", "GET"),
    ("/healthz", "HEAD"),
    ("/metrics", "GET"),
    ("/metrics", "HEAD"),
    ("/openapi.json", "GET"),
    ("/openapi.json", "HEAD"),
})


def _reserved_paths_for(mcp_obj) -> frozenset[str]:
    """Reserved URL paths for tool registration on this MCP instance.

    Includes the always-open ops paths plus the configured streamable
    HTTP path (default ``/mcp``). Operators who set
    ``mcp.settings.streamable_http_path = '/rpc'`` get the right guard
    automatically (verify ADV-001 / fastapi-phase2 R1).
    """
    extra: set[str] = set()
    settings = getattr(mcp_obj, "settings", None)
    if settings is not None:
        sp = getattr(settings, "streamable_http_path", None)
        if isinstance(sp, str) and sp.startswith("/"):
            extra.add(sp.rstrip("/") or "/")
    extra.add("/mcp")  # Always reserve the default too.
    return _BASE_RESERVED_PATHS | frozenset(extra)


# Back-compat alias — some tests/callers may import the old name.
_RESERVED_PATHS = _BASE_RESERVED_PATHS | frozenset({"/mcp"})


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
        # SDD: public-openapi.
        # Canonicalise the path BEFORE comparing against the public
        # set. Two reasons:
        # 1. Trailing slashes — some Ingress controllers append `/`,
        #    we want /healthz/ to match /healthz.
        # 2. MF-7 (path-confusion attack) — `request.url.path` is
        #    Starlette's parsed component (URL-decoded once, split
        #    from query). It does NOT collapse `..` segments, so a
        #    request like `GET /openapi.json/../mcp` arrives with
        #    `path == "/openapi.json/../mcp"` (literal). Exact match
        #    against PUBLIC_ENDPOINTS rejects it -> falls through to
        #    auth -> 401. Verified by
        #    test_path_traversal_request_path_is_literal.
        # 3. MF-8 (method confusion) — listing (path, METHOD) tuples
        #    means POST /openapi.json is NOT public (FastAPI returns
        #    405 because no POST handler is registered).
        canon = request.url.path.rstrip("/") or "/"
        method = request.method.upper()
        if (canon, method) in PUBLIC_ENDPOINTS:
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
        # Verify R4 / ADV-005: even when there are no defs, return a
        # deep copy so the cached doc is decoupled from later mutation
        # of the source tool's parameters dict.
        return copy.deepcopy(schema)

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
                    if isinstance(inlined, dict):
                        # Preserve sibling keywords (description,
                        # default, title, deprecated, examples, etc.)
                        # on the original $ref node — JSON Schema
                        # 2020-12 allows them and OpenAPI carries
                        # semantic meaning (verify R3 / ADV-002).
                        merged = dict(inlined)
                        for k, v in node.items():
                            if k == "$ref":
                                continue
                            merged.setdefault(k, walk(v))
                        return merged
                    return inlined
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out = walk(copy.deepcopy(schema))
    if isinstance(out, dict):
        # If a recursive ref survived (cycle), KEEP $defs so the
        # remaining $ref still resolves — dangling refs would break
        # OpenWebUI's importer (verify ADV-002 / fastapi-phase2 R1).
        leftover = json.dumps(out)
        has_cycle_ref = (
            "#/$defs/" in leftover or "#/definitions/" in leftover
        )
        if not has_cycle_ref:
            out.pop("$defs", None)
            out.pop("definitions", None)
    return out


def _unwrap_call_tool_result(result: Any) -> Any:
    """Convert a FastMCP tool-run result to a JSON-friendly value.

    Preference order:
    1. ``result.structuredContent`` if present.
    2. If content list is all-text, concatenate ``.text`` and try
       ``json.loads``; fall back to ``{"text": ...}``.
    3. If content is mixed/non-text (e.g. ImageContent), return a list
       of ``{"type": ..., "text"|"data": ...}`` envelopes preserving
       every item (verify R3 / ADV-002).
    4. ``str(result)`` envelope as last resort.
    """
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    content = getattr(result, "content", None)
    if content is None and isinstance(result, list):
        content = result
    if isinstance(content, list) and content:
        all_text = all(
            getattr(item, "text", None) is not None for item in content
        )
        if all_text:
            joined = "".join(
                getattr(item, "text", "") for item in content
            )
            try:
                return json.loads(joined)
            except (json.JSONDecodeError, ValueError):
                return {"text": joined}
        # Mixed / non-text: preserve every item as a JSON envelope so
        # clients can consume image/audio/resource content too.
        out = []
        for item in content:
            envelope: dict = {
                "type": getattr(item, "type", type(item).__name__),
            }
            for attr in ("text", "data", "mimeType", "uri", "name"):
                v = getattr(item, attr, None)
                if v is not None:
                    envelope[attr] = v
            out.append(envelope)
        return {"content": out}
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
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                # JSON parse failures and body-decoding failures
                # (e.g. invalid UTF-8 with content-type:
                # application/json) must surface as 400, not 500.
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
        except Exception as exc:
            # FastMCP wraps argument-validation failures in
            # ToolError. ONLY ValidationError-from-cause is a 400;
            # any other exception (including TypeError raised inside
            # the tool body, e.g. ``1 + 'a'``) is a server bug and
            # MUST be a 500 (verify ADV-008 / fastapi-phase2 R1).
            cause = getattr(exc, "__cause__", None)
            if isinstance(cause, ValidationError):
                return JSONResponse(
                    {"error": f"ValidationError: {cause}"},
                    status_code=400,
                )
            # Last-resort envelope. Traceback intentionally NOT in body.
            # Generic body keeps internal URLs / file paths / upstream
            # payloads out of HTTP responses (CR review on PR #16).
            # Server-side log retains the full exception for diagnostics.
            logger.exception("tool %r raised", tool.name)
            return JSONResponse(
                {"error": "internal server error"},
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
                {"error": "internal server error"},
                status_code=500,
            )

    handler.__name__ = f"tool_{tool.name}"
    return handler


def _build_openapi_doc(
    tools,
    *,
    server_title: str = "homelab-mcp",
    server_version: str = "phase2",
    reserved_paths: frozenset = _RESERVED_PATHS,
) -> dict:
    """Build a tools-only OpenAPI 3.1 document (mcpo-compatible)."""
    paths: dict = {}
    for tool in tools:
        if not _TOOL_NAME_RE.match(tool.name):
            continue
        if f"/{tool.name}" in reserved_paths:
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
    reserved = _reserved_paths_for(mcp_obj)
    list_failed = False

    def _enumerate(tm) -> tuple[list, bool]:
        """Enumerate tools with fallback to ``_tools`` (verify R4).

        Mirrors ``_tool_count``'s shape: prefer ``list_tools()`` if
        callable; on failure or when not callable, fall back to the
        private ``_tools`` dict so we still expose tools instead of
        going dark. If the primary accessor RAISED (not just absent),
        the returned list_failed flag stays True so /openapi.json
        signals degradation consistently with /healthz (CR review on
        PR #16: don't let the fallback silently mask a real failure).
        """
        primary_failed = False
        list_fn = getattr(tm, "list_tools", None)
        if callable(list_fn):
            try:
                return list(list_fn()), False
            except Exception:
                primary_failed = True
                logger.exception(
                    "list_tools failed; trying private _tools fallback"
                )
        # Fallback: private _tools dict.
        tm_tools = getattr(tm, "_tools", None)
        if tm_tools is not None:
            try:
                if hasattr(tm_tools, "values"):
                    return list(tm_tools.values()), primary_failed
                return list(tm_tools), primary_failed
            except Exception:
                logger.exception("_tools fallback also failed")
        return [], True

    if tm is None:
        logger.warning(
            "tool manager unavailable; OpenAPI mirror skipped"
        )
        list_failed = True
        tools: list = []
    else:
        tools, list_failed = _enumerate(tm)

    seen_names: set[str] = set()
    for tool in tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not _TOOL_NAME_RE.match(name):
            logger.warning("skip tool %r: invalid HTTP name", name)
            continue
        if f"/{name}" in reserved:
            logger.warning(
                "skip tool %r: collides with reserved path", name
            )
            continue
        if name in seen_names:
            # Verify R4 / ADV-001: duplicate tool names would let
            # add_api_route keep both handlers while _build_openapi_doc
            # advertises only one. Refuse the second — startup
            # surfaces the collision via /openapi.json (degraded).
            logger.error(
                "duplicate tool name %r; OpenAPI mirror degraded", name
            )
            list_failed = True
            continue
        seen_names.add(name)
        app.add_api_route(
            f"/{name}",
            _make_tool_handler(tool),
            methods=["POST"],
            name=f"tool_{name}",
            include_in_schema=False,
        )
        registered.append(tool)

    # Cache the doc on app.state so tests can override it.
    app.state.openapi_doc = _build_openapi_doc(
        registered, reserved_paths=reserved
    )
    # When the tool manager couldn't be enumerated at startup, the
    # mirror is in a degraded state. Surface it via /openapi.json so
    # clients (OpenWebUI's importer) don't treat the empty registry
    # as a valid "this server has zero tools" answer (verify R3 /
    # BUG-004).
    app.state.openapi_mirror_degraded = list_failed

    async def openapi_json(request: Request):
        # SDD: public-openapi — reachable without auth (see PUBLIC_ENDPOINTS
        # and auth_mw). HEAD support is explicit so curl -I and load-balancer
        # probes work; Starlette does not auto-dispatch HEAD onto GET-only
        # routes registered via @app.get, so we register both methods here.
        if getattr(app.state, "openapi_mirror_degraded", False):
            return JSONResponse(
                {
                    "error": "tool manager unavailable",
                    "openapi_doc": app.state.openapi_doc,
                },
                status_code=503,
            )
        return app.state.openapi_doc

    app.add_api_route(
        "/openapi.json",
        openapi_json,
        methods=["GET", "HEAD"],
        include_in_schema=False,
        name="openapi_json",
    )


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
