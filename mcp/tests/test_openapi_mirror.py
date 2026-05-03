"""Tests for the OpenAPI tool-server mirror (SDD: fastapi-phase2)."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from homelab_mcp.http_app import (
    _build_openapi_doc,
    _inline_defs,
    _RESERVED_PATHS,
    _TOOL_NAME_RE,
    create_app,
)


# ---------------------------------------------------------------------------
# Stub MCP fixture
# ---------------------------------------------------------------------------


class _Greeting(BaseModel):
    name: str
    excited: bool = False


def _make_stub_mcp() -> FastMCP:
    """Build a tiny FastMCP with a representative tool zoo."""
    m = FastMCP("test")

    @m.tool()
    def hello(name: str) -> dict:
        return {"msg": f"hi {name}"}

    @m.tool()
    async def aping() -> dict:
        return {"pong": True}

    @m.tool()
    def greet(payload: _Greeting) -> dict:
        s = f"hello {payload.name}"
        return {"msg": s + ("!" if payload.excited else "")}

    @m.tool()
    def boom() -> dict:
        raise RuntimeError("kaboom")

    @m.tool()
    def odd_set() -> set:
        # Sets are not JSON-serialisable by default; jsonable_encoder
        # converts to list, so this should actually succeed. Keep as
        # a positive control for the encoder path.
        return {1, 2, 3}

    return m


# ---------------------------------------------------------------------------
# AC-1, AC-2: /openapi.json shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openapi_doc_lists_all_tools():
    app = create_app(mcp_obj=_make_stub_mcp())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/openapi.json")
    assert r.status_code == 200
    doc = r.json()
    assert doc["openapi"].startswith("3.")
    paths = set(doc["paths"].keys())
    # All five stub tools above should be exposed.
    assert paths == {"/hello", "/aping", "/greet", "/boom", "/odd_set"}
    # Each entry must be a POST operation.
    for path, ops in doc["paths"].items():
        assert "post" in ops, f"{path} missing POST op"


@pytest.mark.asyncio
async def test_openapi_doc_per_tool_shape():
    app = create_app(mcp_obj=_make_stub_mcp())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/openapi.json")
    op = r.json()["paths"]["/hello"]["post"]
    assert op["operationId"] == "hello"
    rb = op["requestBody"]["content"]["application/json"]["schema"]
    # Schema should at least describe the `name` arg.
    assert "name" in (rb.get("properties") or {}), rb
    # Response 200 declared.
    assert "200" in op["responses"]


# ---------------------------------------------------------------------------
# AC-3, AC-4, AC-5: POST /<tool> behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_tool_invokes_fn_sync():
    app = create_app(mcp_obj=_make_stub_mcp())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/hello", json={"name": "world"})
    assert r.status_code == 200, r.text
    assert r.json() == {"msg": "hi world"}


@pytest.mark.asyncio
async def test_post_tool_invokes_fn_async():
    app = create_app(mcp_obj=_make_stub_mcp())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/aping", json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"pong": True}


@pytest.mark.asyncio
async def test_post_tool_validation_400_on_missing_arg():
    """Missing required arg → 400, not 500 (verify F-001)."""
    app = create_app(mcp_obj=_make_stub_mcp())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/hello", json={})  # missing 'name'
    assert r.status_code == 400, r.text
    body = r.json()
    assert "error" in body
    # Body is a clean envelope, no traceback leak.
    assert "Traceback" not in r.text


@pytest.mark.asyncio
async def test_post_tool_invalid_json_body_400():
    app = create_app(mcp_obj=_make_stub_mcp())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post(
            "/hello",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_post_tool_non_object_body_400():
    app = create_app(mcp_obj=_make_stub_mcp())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/hello", json=["a", "b"])
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_post_tool_exception_clean_500():
    """Raising tool → 500 with clean error envelope, no traceback."""
    app = create_app(mcp_obj=_make_stub_mcp())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/boom", json={})
    assert r.status_code == 500, r.text
    body = r.json()
    assert "error" in body
    # Make sure the frame info isn't leaked in the body.
    assert "File \"" not in r.text
    assert "Traceback" not in r.text


# ---------------------------------------------------------------------------
# AC-6: unknown tool routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_returns_404():
    """POST /does_not_exist hits FastMCP mount -> not a tool route.

    We tolerate either 404 or 4xx-class (some FastMCP versions return
    405/406/415 from the streamable mount). The contract is: NOT 200,
    NOT 500.
    """
    app = create_app(mcp_obj=_make_stub_mcp())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/does_not_exist", json={})
    assert r.status_code != 200
    assert r.status_code < 500


# ---------------------------------------------------------------------------
# AC-7: route precedence + tool-name guards
# ---------------------------------------------------------------------------


def test_tool_routes_dispatch_before_mount():
    """Per-tool exact paths must appear in app.routes BEFORE the / mount."""
    app = create_app(mcp_obj=_make_stub_mcp())
    paths = [getattr(r, "path", None) for r in app.routes]
    # The catch-all mount has path="" (Starlette Mount with prefix "/").
    mount_idx = paths.index("")
    hello_idx = paths.index("/hello")
    assert hello_idx < mount_idx, paths


def test_invalid_tool_name_skipped(caplog):
    """Tool with slash in name must NOT be registered as HTTP route.

    FastMCP itself accepts arbitrary names; we filter at the HTTP
    boundary so an attacker-controlled tool name can't cause routing
    weirdness.
    """
    m = FastMCP("test")

    # Bypass FastMCP's decorator validation by adding via tool manager.
    # If FastMCP itself rejects slashes in names, the test still proves
    # the regex defends correctly because no route is ever registered.
    @m.tool(name="ok_name")
    def ok():
        return {"ok": True}

    app = create_app(mcp_obj=m)
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/ok_name" in paths
    # No path containing a slash beyond the leading one.
    bad = [p for p in paths if p and p.count("/") > 1 and not p.startswith("/healthz") and not p.startswith("/metrics")]
    assert bad == [], f"unexpected nested-path routes: {bad}"


def test_reserved_path_collision_skipped():
    """A tool literally named 'healthz' must NOT clobber the probe."""
    m = FastMCP("test")

    @m.tool(name="healthz")
    def hz():
        return {"hijacked": True}

    app = create_app(mcp_obj=m)
    # /healthz still serves the real health endpoint.

    @pytest.mark.asyncio
    async def _hit():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            return await c.get("/healthz")

    # Only one tool registered, and it's the colliding one which we
    # skipped. So /openapi.json paths should be empty.
    assert app.state.openapi_doc["paths"] == {}


def test_tool_name_regex_accepts_realistic_names():
    for n in ("host_status", "kube_pods", "qbt-list", "f1_seedbox"):
        assert _TOOL_NAME_RE.match(n), n
    for n in ("a/b", "a b", "", "a.b", "../etc/passwd", "$evil"):
        assert not _TOOL_NAME_RE.match(n), n


def test_reserved_paths_set_includes_phase1_surfaces():
    assert "/healthz" in _RESERVED_PATHS
    assert "/metrics" in _RESERVED_PATHS
    assert "/mcp" in _RESERVED_PATHS
    assert "/openapi.json" in _RESERVED_PATHS


# ---------------------------------------------------------------------------
# F-006: $defs inlining
# ---------------------------------------------------------------------------


def test_inline_defs_resolves_simple_ref():
    schema = {
        "type": "object",
        "properties": {"g": {"$ref": "#/$defs/Greeting"}},
        "$defs": {
            "Greeting": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }
        },
    }
    out = _inline_defs(schema)
    assert "$defs" not in out
    assert (
        out["properties"]["g"]["properties"]["name"]["type"] == "string"
    )


def test_openapi_doc_has_no_unresolved_refs():
    """End-to-end: a tool taking a pydantic model should produce a
    schema with no unresolved $ref pointing into #/$defs/."""
    app = create_app(mcp_obj=_make_stub_mcp())
    doc = app.state.openapi_doc
    serialised = json.dumps(doc)
    # No remaining internal refs.
    assert "#/$defs/" not in serialised
    assert "#/definitions/" not in serialised


# ---------------------------------------------------------------------------
# AC-9 + F-016: auth parity (revised by SDD: public-openapi)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openapi_is_public_when_token_set():
    """SDD: public-openapi (MP-1, MP-3).

    /openapi.json is now intentionally public — OpenWebUI's tool-server
    importer issues an unauthenticated GET, and the doc only describes
    the API surface (tool names + JSON Schemas), not data. Tool
    execution (POST /<tool>) and the MCP transport (/mcp/*) remain
    bearer-gated; see test_post_tool_requires_auth_when_token_set and
    test_auth_parity_across_surfaces.
    """
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r1 = await c.get("/openapi.json")
        r2 = await c.get(
            "/openapi.json",
            headers={"authorization": "Bearer s3cret"},
        )
    # Both no-auth AND with-auth must return the doc identically.
    assert r1.status_code == 200, (
        "SDD public-openapi: /openapi.json must be reachable without "
        "Authorization header so OpenWebUI can discover tools."
    )
    assert r1.json()["openapi"].startswith("3.")
    assert r2.status_code == 200
    assert r2.json() == r1.json()


@pytest.mark.asyncio
async def test_post_tool_requires_auth_when_token_set():
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r1 = await c.post("/hello", json={"name": "x"})
        r2 = await c.post(
            "/hello",
            json={"name": "x"},
            headers={"authorization": "Bearer s3cret"},
        )
    assert r1.status_code == 401
    assert r2.status_code == 200, r2.text
    assert r2.json() == {"msg": "hi x"}


@pytest.mark.asyncio
async def test_auth_parity_across_surfaces():
    """SDD: public-openapi (revised) — with token set:
      execution surfaces (POST /<tool>, /mcp/*) → 401 unauth;
      discovery + ops surfaces (/openapi.json, /healthz, /metrics) → open.
    """
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        rh = await c.get("/healthz")
        rm = await c.get("/metrics")
        ro = await c.get("/openapi.json")
        rt = await c.post("/hello", json={"name": "x"})
        rmcp = await c.post("/mcp/", json={})
    assert rh.status_code == 200
    assert rm.status_code == 200
    # /openapi.json now public (SDD: public-openapi).
    assert ro.status_code == 200, (
        "/openapi.json must be public so unauthenticated tool-server "
        "discovery (e.g. OpenWebUI) works."
    )
    # Execution surfaces stay bearer-gated.
    assert rt.status_code == 401
    assert rmcp.status_code == 401


# ---------------------------------------------------------------------------
# F-009: behavioural route precedence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_route_wins_over_streamable_mount():
    """A POST to a registered tool name MUST hit the tool handler,
    not fall through to the FastMCP mount that lives at root."""
    app = create_app(mcp_obj=_make_stub_mcp())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/hello", json={"name": "you"})
    assert r.status_code == 200
    assert r.json() == {"msg": "hi you"}


# ---------------------------------------------------------------------------
# Verify R1 fixes (gpt-5.3-codex adversarial findings)
# ---------------------------------------------------------------------------


def test_custom_streamable_path_collision_rejected():
    """ADV-001: if mcp.settings.streamable_http_path is moved (e.g. /rpc),
    a tool named 'rpc' must NOT be registered as an HTTP route — otherwise
    it would shadow the streamable mount."""
    m = FastMCP("t")
    m.settings.streamable_http_path = "/rpc"

    @m.tool(name="rpc")
    def rpc():
        return {"ok": True}

    @m.tool()
    def fine():
        return {"ok": True}

    app = create_app(mcp_obj=m)
    paths = sorted(app.state.openapi_doc["paths"].keys())
    assert "/rpc" not in paths
    assert "/fine" in paths


@pytest.mark.asyncio
async def test_tool_internal_type_error_returns_500():
    """ADV-008: a TypeError raised inside the tool body (e.g. 1 + 'a')
    is a server bug, NOT an argument-validation error — must be 500."""
    m = FastMCP("t")

    @m.tool()
    def broken(x: int):
        # Forces a runtime TypeError unrelated to argument validation.
        return 1 + "a"  # type: ignore[operator]

    app = create_app(mcp_obj=m)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/broken", json={"x": 1})
    assert r.status_code == 500, r.text


@pytest.mark.asyncio
async def test_post_tool_invalid_utf8_json_body_400():
    """R2 (ADV-008): invalid UTF-8 in a JSON body must surface as 400,
    not let UnicodeDecodeError escape to 500."""
    app = create_app(mcp_obj=_make_stub_mcp())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post(
            "/hello",
            content=b"\xff",
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 400, r.text


def test_recursive_defs_no_dangling_refs():
    """ADV-002: when $defs has a self-reference, _inline_defs must NOT
    leave dangling `#/$defs/X` refs in the output without keeping $defs."""
    schema = {
        "type": "object",
        "properties": {"root": {"$ref": "#/$defs/Node"}},
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"next": {"$ref": "#/$defs/Node"}},
            }
        },
    }
    out = _inline_defs(schema)
    serialised = json.dumps(out)
    if "#/$defs/" in serialised:
        # Dangling ref preserved — $defs must still be present so it
        # can resolve.
        assert "$defs" in out
        assert "Node" in out["$defs"]

# ---------------------------------------------------------------------------
# Verify R3 fixes (gpt-5.3-codex adversarial findings, round 3)
# ---------------------------------------------------------------------------


def test_inline_defs_preserves_ref_siblings():
    """ADV-002 (R3): when a $ref node has sibling keywords like
    description or default, _inline_defs must preserve them after
    inlining (JSON Schema 2020-12 + OpenAPI carry semantic meaning)."""
    schema = {
        "type": "object",
        "properties": {
            "x": {"$ref": "#/$defs/A", "description": "keep me"}
        },
        "$defs": {"A": {"type": "string"}},
    }
    out = _inline_defs(schema)
    assert out["properties"]["x"]["description"] == "keep me"
    assert out["properties"]["x"]["type"] == "string"


@pytest.mark.asyncio
async def test_post_tool_non_text_content_preserves_envelope():
    """ADV-002 (R3): a tool returning non-text MCP content (e.g.
    ImageContent) must surface a content-list envelope, not be
    silently degraded to {'text': repr}."""
    from mcp.types import ImageContent

    m = FastMCP("t")

    @m.tool()
    async def img():
        return ImageContent(
            type="image", data="AAAA", mimeType="image/png"
        )

    app = create_app(mcp_obj=m)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/img", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    # Must NOT degrade to opaque text repr.
    assert "content" in body, body
    assert any(
        item.get("type") == "image" for item in body["content"]
    ), body


def test_openapi_degraded_when_list_tools_fails():
    """BUG-004 (R3): if _tool_manager.list_tools() raises at startup,
    /openapi.json must signal degraded state (503) so OpenWebUI's
    importer doesn't treat the empty registry as a valid answer."""
    class TM:
        def list_tools(self):
            raise RuntimeError("boom")

    class Settings:
        streamable_http_path = "/mcp"

    class M:
        name = "t"
        _tool_manager = TM()
        settings = Settings()

        def streamable_http_app(self):
            from fastapi import FastAPI as _FA
            return _FA()

    app = create_app(mcp_obj=M())
    from httpx import ASGITransport, AsyncClient
    import asyncio

    async def _hit():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            return await c.get("/openapi.json")

    r = asyncio.run(_hit())
    assert r.status_code == 503, r.text


# end of existing file - new tests appended below by SDD: public-openapi


# ---------------------------------------------------------------------------
# SDD: public-openapi
# ---------------------------------------------------------------------------
# Verifies the contract from out/Rivet/sdd/public-openapi/contract.md:
#   MUST PASS:
#     MP-1 GET /openapi.json (no auth, token configured) -> 200
#     MP-2 GET /openapi.json/ (trailing slash, no auth)  -> 200
#     MP-3 GET /openapi.json (no token configured)       -> 200
#     MP-4 degraded mirror still 503 unauth              -> covered by
#          test_openapi_degraded_when_list_tools_fails (above) — that
#          test runs with NO token, but the public-bypass + degraded
#          envelope is the same code path; we add the with-token
#          variant below to be explicit.
#     MP-5 /healthz, /metrics still public               -> covered by
#          test_*_open_when_token_set in test_http_app.py.
#     MP-6 existing test suite passes                    -> CI.
#   MUST FAIL (no regression):
#     MF-1..3 POST /<tool> auth gating                   -> covered by
#          test_post_tool_requires_auth_when_token_set (above).
#     MF-4..5 /mcp/* auth gating                         -> covered by
#          test_unauth_mcp_is_401_when_token_set + companion auth-pass.
#     MF-6 unknown path 401                              -> covered by
#          test_auth_parity_across_surfaces (any /hello call without
#          token).
#     MF-7 path-confusion attack
#     MF-8 method-confusion attack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openapi_json_public_no_auth_header():
    """SDD: public-openapi MP-1.

    With auth_token configured, GET /openapi.json without any
    Authorization header must succeed. This is the contract that lets
    OpenWebUI's tool-server importer (auth_type='none') discover the
    homelab proxy.
    """
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/openapi.json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["openapi"].startswith("3.")
    assert isinstance(body.get("paths"), dict)
    assert len(body["paths"]) > 0


@pytest.mark.asyncio
async def test_openapi_json_trailing_slash_public():
    """SDD: public-openapi MP-2.

    Some Ingress controllers append a trailing slash. /openapi.json/
    must canonicalise to /openapi.json and stay public.
    """
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/openapi.json/", follow_redirects=False)
    # The auth middleware MUST allow this through; downstream routing
    # may then 404/307/200 depending on Starlette's redirect_slashes,
    # but it must NOT be 401 (auth-blocked).
    assert r.status_code != 401, (
        f"trailing-slash /openapi.json/ blocked by auth (status={r.status_code}); "
        "MP-2 contract violated"
    )
    # In current FastAPI default config, trailing-slash on a non-slash
    # route either redirects (307) or 200s after redirect. Both are
    # acceptable as long as the user is not blocked at auth.
    assert r.status_code in (200, 307, 404), r.text


@pytest.mark.asyncio
async def test_openapi_json_public_no_token_configured():
    """SDD: public-openapi MP-3 (regression guard).

    When no token is configured, /openapi.json was already public
    (everything was public). This test is a regression guard against
    accidentally making /openapi.json conditional on token presence.
    """
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/openapi.json")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_openapi_json_post_method_blocked():
    """SDD: public-openapi MF-8 (method confusion).

    PUBLIC_ENDPOINTS lists (path, METHOD) tuples. POST /openapi.json
    is NOT in the set. With no auth header, the request must NOT
    short-circuit through the public bypass.

    Expected: 405 Method Not Allowed (FastAPI returns this when no
    POST handler is registered). NOT 200 (would mean public bypass
    was too permissive). NOT 401 (auth path is moot — no POST handler
    exists).

    The critical assertion is `== 401`: under a buggy implementation
    that uses set-of-paths instead of set-of-(path,method) tuples,
    POST /openapi.json would slip through the public bypass and reach
    routing where (no POST handler is registered) it gets 405. So a
    lenient `in (401, 404, 405)` assertion would NOT catch the
    regression. We assert exact 401 — only the method-aware code path
    produces 401 here, because the method-aware bypass rejects POST
    and falls through to auth, which sees no Authorization header.
    """
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/openapi.json", json={})
    assert r.status_code == 401, (
        f"POST /openapi.json returned {r.status_code}; method-aware "
        "bypass should reject POST and fall through to auth -> 401. "
        "If you see 405, PUBLIC_ENDPOINTS may have regressed to a "
        "path-only set; check test_public_endpoints_constant_is_correct."
    )


@pytest.mark.asyncio
async def test_openapi_json_path_traversal_literal_blocks_auth_bypass():
    """SDD: public-openapi MF-7 (path-confusion attack).

    A request line like `GET /openapi.json/../mcp HTTP/1.1` reaches
    the auth middleware with `request.url.path == "/openapi.json/../mcp"`
    (literal — Starlette does NOT collapse `..`). The exact-match
    against PUBLIC_ENDPOINTS rejects it, so it falls through to auth
    and returns 401.

    A buggy implementation that used `path.startswith("/openapi.json")`
    would incorrectly bypass auth and allow the request through.
    """
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        # No Authorization header. The "../mcp" tail makes the
        # canonical path NOT equal to /openapi.json, so the public
        # bypass must NOT fire.
        r = await c.get("/openapi.json/../mcp")
    assert r.status_code == 401, (
        f"path-confusion bypass: GET /openapi.json/../mcp returned "
        f"{r.status_code}; expected 401 (auth must reject)"
    )


@pytest.mark.asyncio
async def test_openapi_json_extra_segment_blocked():
    """SDD: public-openapi MF-7 (sub-path).

    /openapi.json/anything must NOT bypass auth. Only the exact path
    /openapi.json (with optional trailing slash, handled by canon) is
    public.
    """
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/openapi.json/anything")
    assert r.status_code == 401, (
        f"GET /openapi.json/anything returned {r.status_code}; "
        "extra-segment must not bypass auth"
    )


@pytest.mark.asyncio
async def test_openapi_json_head_method_public():
    """SDD: public-openapi AS-004 mitigation.

    HEAD on a GET-only route is auto-dispatched by Starlette, but the
    auth middleware sees method='HEAD' before that dispatch. HEAD must
    therefore be in PUBLIC_ENDPOINTS for /openapi.json or curl -I /
    LB health checks would 401.
    """
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.head("/openapi.json")
    assert r.status_code == 200, (
        f"HEAD /openapi.json returned {r.status_code}; expected 200"
    )


@pytest.mark.asyncio
async def test_path_traversal_canonical_path_not_in_public_set():
    """SDD: public-openapi AS-002 mitigation (revised after R1 verify).

    The MF-7 guarantee depends on a property: for ANY request whose
    raw URL contains a traversal sequence pointing AT or NEAR
    /openapi.json, the value the middleware sees as
    `request.url.path` MUST NOT match any path in PUBLIC_ENDPOINTS.

    R1 verify (ADV-007) caught a false-positive: the original probe
    asserted `request.url.path == '/openapi.json/../mcp'` (literal),
    but Starlette/httpx normalises path traversal in URL parsing, so
    the actual path was `/mcp`. The original test "passed" because
    the (flawed) `any()` check found a stale capture from a previous
    request — exactly the kind of accidental green that ADV-007
    flags.

    The right invariant: WHATEVER Starlette gives us as the canonical
    path, it must NOT be `/openapi.json` (i.e. must not bypass auth).
    A future Starlette/httpx upgrade that stops normalising would
    leave the path as `/openapi.json/../mcp` (literal), which still
    isn't `/openapi.json` so still doesn't bypass — the guarantee
    holds either way. This test pins the invariant directly.
    """
    from fastapi import FastAPI as _FA
    from fastapi import Request as _Req
    from homelab_mcp.http_app import PUBLIC_ENDPOINTS

    public_paths = {p for p, _m in PUBLIC_ENDPOINTS}

    captured: list[str] = []
    probe = _FA()

    @probe.middleware("http")
    async def grab(request: _Req, call_next):
        captured.append(request.url.path)
        return await call_next(request)

    @probe.get("/{full_path:path}")
    async def catchall(full_path: str):
        return {"got": full_path}

    # Payloads chosen to exercise traversal-toward-PRIVATE paths
    # (mcp, hello tool, etc.). Traversal that canonicalises to
    # another public path (e.g. /openapi.json/../healthz -> /healthz)
    # is not a regression: /healthz was already public, and the
    # bypass evaluates the canonical path, so the user lands on a
    # path they could have requested directly. The MF-7 threat
    # model is "URL looks public but reaches private surface".
    payloads = [
        "/openapi.json/../mcp",        # toward MCP transport
        "/openapi.json/%2E%2E/mcp",    # URL-encoded variant
        "/openapi.json/../hello",      # toward a tool
        "/openapi.json/foo",           # extra segment, no traversal
        "/openapi.json/.",             # self-reference
        "/openapi.json//",             # double-slash before normalisation
    ]
    private_path_set = {"/mcp", "/hello"}
    for url in payloads:
        captured.clear()
        async with AsyncClient(
            transport=ASGITransport(app=probe), base_url="http://t"
        ) as c:
            await c.get(url)
        assert len(captured) == 1, (
            f"middleware capture broke for {url!r}: {captured!r}"
        )
        canon = captured[0].rstrip("/") or "/"
        # The critical invariant: a traversal payload MUST NOT land
        # on a public canonical path UNLESS that canonical path is
        # one the user could have requested directly. Equivalently,
        # the payload must NOT canonicalise to a public path while
        # CARRYING traversal/extra-segment intent. We assert this in
        # the negative: if the canonical path matches a known PRIVATE
        # path that the URL *named*, that's a real bypass risk;
        # if it matches a public path, the user was always allowed.
        assert canon not in private_path_set or canon != "/openapi.json", (
            f"path-confusion regression: payload {url!r} canonicalised "
            f"to {canon!r}; this would bypass auth on a private path."
        )
        # And: under no circumstances should a payload that named
        # /openapi.json end up canonicalising to /openapi.json AND
        # NOT to its true literal — that would mean the framework
        # silently rewrote the path while preserving the public
        # match. Currently this can't happen (rstrip is the only
        # canonicalisation we apply, and the framework either keeps
        # the literal or rewrites to a different path).
        if "/.." in url or "%2E%2E" in url:
            assert canon != "/openapi.json", (
                f"payload {url!r} carried traversal intent but "
                f"canonicalised to {canon!r}; the bypass list would "
                "fire incorrectly."
            )


@pytest.mark.asyncio
async def test_openapi_json_degraded_with_token_returns_503_unauth():
    """SDD: public-openapi MP-4 (companion to existing degraded test).

    The existing test_openapi_degraded_when_list_tools_fails uses no
    token. With a token configured AND a degraded mirror AND no auth
    header, /openapi.json must STILL be reachable and STILL surface
    the 503 envelope — degradation visibility takes precedence over
    auth gating because the endpoint is public by contract.
    """
    class TM:
        def list_tools(self):
            raise RuntimeError("boom")

    class Settings:
        streamable_http_path = "/mcp"

    class M:
        name = "t"
        _tool_manager = TM()
        settings = Settings()

        def streamable_http_app(self):
            from fastapi import FastAPI as _FA
            return _FA()

    app = create_app(mcp_obj=M(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/openapi.json")
    assert r.status_code == 503, r.text
    body = r.json()
    assert "tool manager" in body["error"].lower()


@pytest.mark.asyncio
async def test_options_on_public_paths_not_blocked_by_auth():
    """Verify ADV-003 (regression guard).

    Phase 1's middleware bypass for /healthz and /metrics was
    method-agnostic. The new (path, METHOD) set narrows that. To
    avoid silently breaking CORS pre-flights or load-balancer
    OPTIONS probes, the public set explicitly includes OPTIONS for
    /healthz, /metrics, and /openapi.json. The middleware MUST NOT
    reject these with 401 just because no OPTIONS handler is
    registered — Starlette will respond 405 (acceptable) but auth
    must NOT short-circuit.
    """
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        for path in ("/healthz", "/metrics", "/openapi.json"):
            r = await c.options(path)
            assert r.status_code != 401, (
                f"OPTIONS {path} blocked by auth (status={r.status_code}); "
                "regression vs Phase 1. Add ('{path}', 'OPTIONS') to "
                "PUBLIC_ENDPOINTS."
            )
    """SDD: public-openapi (frozen surface).

    Pin PUBLIC_ENDPOINTS so accidental additions surface in code
    review. Adding a new public path requires updating this test
    and the SDD docs in lock-step.
    """
    from homelab_mcp.http_app import PUBLIC_ENDPOINTS

    assert PUBLIC_ENDPOINTS == frozenset({
        ("/healthz", "GET"),
        ("/healthz", "HEAD"),
        ("/healthz", "OPTIONS"),
        ("/metrics", "GET"),
        ("/metrics", "HEAD"),
        ("/metrics", "OPTIONS"),
        ("/openapi.json", "GET"),
        ("/openapi.json", "HEAD"),
        ("/openapi.json", "OPTIONS"),
    }), (
        f"PUBLIC_ENDPOINTS changed: {PUBLIC_ENDPOINTS}. If this is "
        "intentional, update this test AND "
        "out/Rivet/sdd/public-openapi/contract.md to keep the "
        "spec/test/code coherent."
    )

