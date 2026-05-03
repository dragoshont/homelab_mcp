"""Tests for the FastAPI HTTP transport (SDD: fastapi-phase1)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from homelab_mcp.http_app import create_app


@pytest.mark.asyncio
async def test_healthz_returns_ok_and_tool_count():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["name"] == "homelab"
    assert body["tools"] > 0, "no tools registered — bundle import failed?"


@pytest.mark.asyncio
async def test_metrics_format_is_prom():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    assert "homelab_mcp_up 1" in body
    assert "homelab_mcp_tools_total " in body
    # Both expected HELP lines present.
    assert "# HELP homelab_mcp_up" in body
    assert "# HELP homelab_mcp_tools_total" in body


@pytest.mark.asyncio
async def test_unauth_mcp_is_401_when_token_set():
    app = create_app(auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/mcp/", json={})
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}
    # RFC 6750 §3: 401 on Bearer-protected resource MUST advertise the
    # challenge via WWW-Authenticate. Some HTTP clients (e.g. curl
    # --anyauth, browsers, proxies) rely on this to negotiate auth.
    www_auth = r.headers.get("www-authenticate", "")
    assert www_auth.lower().startswith("bearer"), (
        f"missing/invalid WWW-Authenticate header: {www_auth!r}"
    )


@pytest.mark.asyncio
async def test_authenticated_mcp_post_passes_middleware():
    """Companion to the 401 test (AS-007 mitigation, F-007 in as-findings).

    A correct token MUST get past the middleware AND reach the mounted
    /mcp app. ADV-007 hardening: explicitly reject 404/405, which would
    indicate the mount itself is broken (test passing for the wrong
    reason).
    """
    app = create_app(auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post(
            "/mcp/",
            json={},
            headers={"Authorization": "Bearer s3cret"},
        )
    assert r.status_code != 401, (
        "auth middleware blocked a request with the correct token"
    )
    assert r.status_code not in (404, 405), (
        f"/mcp mount missing or wrong (status={r.status_code}): "
        "the middleware passed but routing is broken"
    )


@pytest.mark.asyncio
async def test_no_token_means_no_auth_on_healthz():
    app = create_app(auth_token=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/healthz")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_healthz_open_even_when_token_set():
    """Probes MUST stay open regardless of auth config (spec A6, F-009)."""
    app = create_app(auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/healthz")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_healthz_trailing_slash_open_when_token_set():
    """Verify ADV-004/ADV-006/ADV-007: trailing-slash probe paths must
    serve the same payload as the canonical path.

    Some Ingress controllers / Cloudflared / curl-with-redirect-follow
    append a trailing slash. Those requests still reach the auth
    middleware before FastAPI's redirect_slashes can issue a 307, AND
    the catch-all /-mount otherwise swallows them and 404s.
    """
    app = create_app(auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/healthz/", follow_redirects=False)
    assert r.status_code in (200, 503), (
        f"trailing-slash /healthz/ returned {r.status_code}; expected the "
        "same status as /healthz (200 happy path, 503 zero tools)"
    )


@pytest.mark.asyncio
async def test_metrics_trailing_slash_open_when_token_set():
    app = create_app(auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/metrics/", follow_redirects=False)
    assert r.status_code == 200, (
        f"trailing-slash /metrics/ returned {r.status_code}; expected 200"
    )
    assert "homelab_mcp_up" in r.text


@pytest.mark.asyncio
async def test_lowercase_bearer_scheme_is_accepted():
    """Verify BUG-007: scheme token is case-insensitive (RFC 7235/6750)."""
    app = create_app(auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post(
            "/mcp/", json={}, headers={"Authorization": "bearer s3cret"}
        )
    assert r.status_code != 401, (
        "lowercase 'bearer' rejected — auth must accept any-case scheme"
    )
    assert r.status_code not in (404, 405), (
        f"/mcp mount missing or wrong (status={r.status_code})"
    )


@pytest.mark.asyncio
async def test_run_uvicorn_strips_token_whitespace(monkeypatch):
    """Verify ADV-004 (R2): env-sourced tokens often carry trailing
    newlines from secret extraction. The configured token must be
    stripped at startup so legitimate clients can authenticate.
    """
    captured = {}

    def fake_create_app(*, auth_token=None, **_):
        captured["token"] = auth_token
        # Return a minimal ASGI app so uvicorn.run gets a callable.
        from fastapi import FastAPI
        return FastAPI()

    def fake_uvicorn_run(app, **_):
        captured["ran"] = True

    monkeypatch.setenv("HOMELAB_MCP_HTTP_TOKEN", "s3cret\n")
    monkeypatch.setenv("HOMELAB_MCP_HTTP_PORT", "8080")
    monkeypatch.setattr("homelab_mcp.http_app.create_app", fake_create_app)

    import uvicorn as _uvicorn
    monkeypatch.setattr(_uvicorn, "run", fake_uvicorn_run)

    from homelab_mcp.http_app import run_uvicorn
    run_uvicorn()

    assert captured["token"] == "s3cret", (
        f"token not stripped: {captured.get('token')!r}"
    )
    assert captured.get("ran") is True


def test_run_uvicorn_whitespace_only_token_fails_closed(monkeypatch):
    """Verify ADV-004 (R4): a token that is set but contains only
    whitespace MUST refuse to start. Silently dropping to no-auth
    would expose /mcp/* on misconfigured deployments.
    """
    monkeypatch.setenv("HOMELAB_MCP_HTTP_TOKEN", "   \n")
    monkeypatch.setenv("HOMELAB_MCP_HTTP_PORT", "8080")

    ran = {"called": False}

    def fake_uvicorn_run(*_a, **_k):
        ran["called"] = True

    import uvicorn as _uvicorn
    monkeypatch.setattr(_uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr(
        "homelab_mcp.http_app.create_app", lambda **_: object()
    )

    from homelab_mcp.http_app import run_uvicorn
    with pytest.raises(SystemExit) as exc:
        run_uvicorn()
    assert exc.value.code == 2
    assert ran["called"] is False, (
        "uvicorn.run was called with auth silently disabled — "
        "fail-closed contract violated"
    )


@pytest.mark.parametrize("bad_port", ["-1", "0", "65536", "70000"])
def test_run_uvicorn_out_of_range_port_rejected(monkeypatch, bad_port):
    """Verify ADV-002 (R4): in-range int validation. Out-of-range
    values pass int() but fail later inside uvicorn.bind() with a
    less actionable traceback.
    """
    monkeypatch.setenv("HOMELAB_MCP_HTTP_PORT", bad_port)
    monkeypatch.delenv("HOMELAB_MCP_HTTP_TOKEN", raising=False)

    ran = {"called": False}

    def fake_uvicorn_run(*_a, **_k):
        ran["called"] = True

    import uvicorn as _uvicorn
    monkeypatch.setattr(_uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr(
        "homelab_mcp.http_app.create_app", lambda **_: object()
    )

    from homelab_mcp.http_app import run_uvicorn
    with pytest.raises(SystemExit) as exc:
        run_uvicorn()
    assert exc.value.code == 2
    assert ran["called"] is False


@pytest.mark.asyncio
async def test_healthz_zero_tools_returns_503():
    """Verify BUG-004: empty tool registry must signal degraded.

    K8s liveness probes only inspect HTTP status. A pod whose tool
    registration silently failed (e.g. driver import error) would
    otherwise stay alive indefinitely.
    """
    from starlette.applications import Starlette

    class EmptyMCP:
        name = "stub"

        def streamable_http_app(self):
            return Starlette()

    app = create_app(mcp_obj=EmptyMCP())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["tools"] == 0
    assert body["status"] == "degraded"


@pytest.mark.asyncio
async def test_healthz_list_tools_exception_returns_503():
    """Verify BUG-004 (R5): if tm.list_tools() raises but the cached
    _tools dict still has entries, healthz must NOT report ok. The
    primary accessor erroring is a real symptom of a broken tool
    manager and should restart the pod.
    """
    from starlette.applications import Starlette

    class BadTM:
        _tools = {"x": object(), "y": object()}  # cached dict still has entries

        def list_tools(self):
            raise RuntimeError("boom")

    class BrokenMCP:
        name = "broken"
        _tool_manager = BadTM()

        def streamable_http_app(self):
            return Starlette()

    app = create_app(mcp_obj=BrokenMCP())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/healthz")
    assert r.status_code == 503, (
        f"healthz reported {r.status_code} despite list_tools raising"
    )
    body = r.json()
    assert body["status"] == "degraded"
    assert body.get("reason") == "tool_manager_unreachable"


@pytest.mark.asyncio
async def test_create_app_strips_token_whitespace():
    """Verify ADV-004 (R5): create_app() must normalize the configured
    token, not only run_uvicorn(). Library/test callers should not be
    bricked when they pass a token with a stray newline.
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse as StarletteJSON
    from starlette.routing import Route

    async def echo_ok(_):
        return StarletteJSON({"ok": True})

    class StubMCP:
        def streamable_http_app(self):
            return Starlette(routes=[Route("/mcp", echo_ok, methods=["POST"])])

    app = create_app(auth_token="s3cret\n", mcp_obj=StubMCP())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post(
            "/mcp", headers={"Authorization": "Bearer s3cret"}
        )
    assert r.status_code != 401, (
        "configured token with trailing newline bricked auth — "
        "create_app must normalize the token like run_uvicorn does"
    )
