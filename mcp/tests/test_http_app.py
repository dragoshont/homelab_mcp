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

    A correct token MUST get past the middleware. We don't assert a
    specific success status because Streamable HTTP can return 200,
    202, or even 400 for an empty body — but it MUST NOT be 401.
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
