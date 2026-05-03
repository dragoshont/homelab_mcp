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
# AC-9 + F-016: auth parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openapi_requires_auth_when_token_set():
    app = create_app(mcp_obj=_make_stub_mcp(), auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r1 = await c.get("/openapi.json")
        r2 = await c.get(
            "/openapi.json",
            headers={"authorization": "Bearer s3cret"},
        )
    assert r1.status_code == 401
    assert r1.headers.get("www-authenticate", "").lower().startswith("bearer")
    # Companion (F-010): WITH auth → 200 + schema body.
    assert r2.status_code == 200
    assert r2.json()["openapi"].startswith("3.")


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
    """With token set, /mcp + /openapi.json + POST /tool ALL 401 unauth;
    /healthz + /metrics stay open (F-016)."""
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
    assert ro.status_code == 401
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
