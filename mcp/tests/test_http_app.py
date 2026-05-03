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
    """Verify ADV-004/ADV-006: trailing-slash probe paths must NOT 401.

    Some Ingress controllers / Cloudflared / curl-with-redirect-follow
    append a trailing slash. Those requests still reach the auth
    middleware before FastAPI's redirect_slashes can issue a 307.
    """
    app = create_app(auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/healthz/", follow_redirects=False)
    assert r.status_code != 401, (
        f"trailing-slash /healthz/ blocked by auth: {r.status_code}"
    )


@pytest.mark.asyncio
async def test_metrics_trailing_slash_open_when_token_set():
    app = create_app(auth_token="s3cret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/metrics/", follow_redirects=False)
    assert r.status_code != 401


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
