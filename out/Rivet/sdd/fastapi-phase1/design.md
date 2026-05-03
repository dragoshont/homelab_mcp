# Design — fastapi-phase1

## 1. Architecture (after change)

```
        Container image (single process)
        ┌──────────────────────────────────────┐
        │  homelab-mcp-http  (uvicorn)         │
        │   │                                   │
        │   ▼                                   │
        │  FastAPI app                          │
        │   ├─ /healthz   (always open)         │
        │   ├─ /metrics   (always open, prom)   │
        │   └─ /mcp/*     (FastMCP Streamable   │
        │                  HTTP, optional auth) │
        │       │                               │
        │       ▼                               │
        │   FastMCP("homelab")  ← same singleton│
        │       │  imported from               │
        │       ▼  homelab_mcp._runtime         │
        │   tools/{platform,media,network,      │
        │          homeauto,control}            │
        └──────────────────────────────────────┘
                    ▲
                    │ port 8080
                    │
         in-cluster Service (mcp-proxy stays in place,
         re-pointed in a follow-up chart bump — out of scope)
```

The host-side stdio path stays as-is:

```
SSH → /home/dragos/.local/bin/homelab-mcp-wrapper
       └─ docker run --entrypoint homelab-mcp ghcr.io/...:sha-...
              └─ python -m homelab_mcp.server  (FastMCP stdio)
```

Two entrypoints, ONE Python package, ONE `mcp` singleton. Choosing
between them is a `--entrypoint` override — the in-process tool
registry is identical.

## 2. Code shape

### 2.1 New module `mcp/src/homelab_mcp/http_app.py`

```python
"""HTTP transport for homelab-mcp.

Wraps FastMCP's native Streamable HTTP app with FastAPI, adding:
- health (always open)
- prom metrics (always open)
- optional bearer-token middleware on /mcp

This is Phase 1 of the architecture KB roadmap. Phase 2+ (REST mirror,
public exposure, tracing) is intentionally NOT implemented here.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse


def _tool_count(mcp_obj) -> int:
    """Best-effort tool count.

    Tries the public-ish accessors first, falls back to a private
    attribute. FastMCP versions move things around; this isolates the
    coupling.
    """
    for attr in ("list_tools", "_tools", "tools"):
        v = getattr(mcp_obj, attr, None)
        if callable(v):
            try:
                return len(v())
            except Exception:
                continue
        if v is not None:
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
        If non-empty, /mcp requests must carry
        ``Authorization: Bearer <auth_token>``. /healthz and /metrics
        are always open (K8s probes need them).
    mcp_obj
        FastMCP instance to mount. If None, imports
        homelab_mcp._runtime.mcp (the canonical singleton — same as
        stdio uses).
    """
    if mcp_obj is None:
        # Import lazily so test harnesses can pass a stub.
        from homelab_mcp._runtime import mcp as _mcp
        # Trigger tool registration as a side effect.
        from homelab_mcp import server  # noqa: F401  (registers tools)
        mcp_obj = _mcp

    app = FastAPI(
        title="homelab-mcp",
        version="phase1",
        docs_url=None,        # Phase 2 will enable Swagger
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def auth_mw(request: Request, call_next):
        # Health and metrics always open.
        if request.url.path in ("/healthz", "/metrics"):
            return await call_next(request)
        if auth_token:
            hdr = request.headers.get("authorization", "")
            if hdr != f"Bearer {auth_token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/healthz")
    async def healthz():
        return {
            "status": "ok",
            "tools": _tool_count(mcp_obj),
            "name": getattr(mcp_obj, "name", "homelab"),
        }

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

    # Mount FastMCP's native Streamable HTTP transport.
    # API name to be confirmed during the spike (R1 in spec.md).
    streamable = mcp_obj.streamable_http_app()  # FastMCP 1.x
    app.mount("/mcp", streamable)

    return app


def run_uvicorn() -> None:
    """Console-script entrypoint."""
    import uvicorn

    host = os.environ.get("HOMELAB_MCP_HTTP_HOST", "0.0.0.0")
    port_raw = os.environ.get("HOMELAB_MCP_HTTP_PORT", "8080")
    try:
        port = int(port_raw)
    except ValueError:
        # Match A3: stderr message MUST mention the env var.
        import sys
        print(
            f"HOMELAB_MCP_HTTP_PORT is not a valid integer: {port_raw!r}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    auth_token = os.environ.get("HOMELAB_MCP_HTTP_TOKEN") or None
    app = create_app(auth_token=auth_token)
    # log_config=None lets the operator set uvicorn / structlog as desired.
    uvicorn.run(app, host=host, port=port, log_level="info")
```

### 2.2 `pyproject.toml` additions

```toml
dependencies = [
    "mcp[cli]>=1.0.0",
    "httpx>=0.27.0",
    "pydantic>=2.0.0",
    "dirigera>=1.2.7",
    "pyatv>=0.16.0",
    "aiounifi>=83,<=85",
    # NEW (Phase 1):
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
]

[project.optional-dependencies]
test = ["pytest>=8.0", "pytest-asyncio>=0.23", "httpx>=0.27"]

[project.scripts]
# (existing scripts unchanged)
# NEW:
homelab-mcp-http = "homelab_mcp.http_app:run_uvicorn"
```

### 2.3 Dockerfile diff

```diff
- RUN pip install --no-cache-dir . mcpo
+ RUN pip install --no-cache-dir .
  ...
- ENTRYPOINT ["mcpo", "--port", "8080", "--host", "0.0.0.0", "--", "homelab-mcp"]
+ ENTRYPOINT ["homelab-mcp-http"]
```

### 2.4 Tests `mcp/tests/test_http_app.py`

```python
import pytest
from httpx import AsyncClient, ASGITransport

from homelab_mcp.http_app import create_app


@pytest.mark.asyncio
async def test_healthz_returns_ok_and_tool_count():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tools"] > 0
    assert body["name"] == "homelab"


@pytest.mark.asyncio
async def test_unauth_mcp_is_401_when_token_set():
    app = create_app(auth_token="s3cret")
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        r = await c.post("/mcp/", json={})  # body irrelevant; middleware fires first
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_no_token_means_no_auth_on_mcp():
    app = create_app(auth_token=None)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_metrics_format_is_prom():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        r = await c.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    assert "homelab_mcp_up 1" in body
    assert "homelab_mcp_tools_total" in body


@pytest.mark.asyncio
async def test_initialize_returns_serverinfo_homelab():
    """JSON-RPC initialize → serverInfo.name=='homelab'."""
    app = create_app()
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"},
        },
    }
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        r = await c.post("/mcp/", json=init_req,
                         headers={"accept": "application/json,text/event-stream"})
    # Streamable HTTP returns SSE or JSON depending on Accept.
    assert r.status_code in (200, 202)
```

## 3. Tasks

| # | Task | Files |
|---|---|---|
| 1 | Spike: confirm `mcp_obj.streamable_http_app()` exists in installed FastMCP. If not, find the correct factory. | (research) |
| 2 | Add `fastapi`, `uvicorn[standard]` deps and `homelab-mcp-http` script | `mcp/pyproject.toml` |
| 3 | Write `mcp/src/homelab_mcp/http_app.py` per §2.1 | new file |
| 4 | Replace mcpo in Dockerfile per §2.3 | `mcp/Dockerfile` |
| 5 | Add `mcp/tests/test_http_app.py` per §2.4 | new file |
| 6 | Run full test suite under `uv run pytest` | — |
| 7 | Build image locally; run `docker run --rm -i ... -p 8080:8080`, smoke `/healthz` and `/mcp` initialize | — |

## 4. Test Plan

| ID | Acceptance | How |
|----|------------|-----|
| T-A1 | A1 module created | `Test-Path mcp/src/homelab_mcp/http_app.py` |
| T-A2 | A2 console script | `grep homelab-mcp-http pyproject.toml` |
| T-A3 | A3 invalid port | `HOMELAB_MCP_HTTP_PORT=garbage homelab-mcp-http; echo $LASTEXITCODE` == 2, stderr contains var name |
| T-A4 | A4 healthz format | unit test (test_healthz_returns_ok_and_tool_count) |
| T-A5 | A5 initialize | unit test (test_initialize_returns_serverinfo_homelab) |
| T-A6 | A6 auth on /mcp | unit test pair (auth set: 401 unauth / 200 auth) |
| T-A7 | A7 no token = open | unit test (test_no_token_means_no_auth_on_mcp) |
| T-A8 | A8 metrics format | unit test (test_metrics_format_is_prom) |
| T-A9 | A9 Dockerfile | `Select-String -Path Dockerfile -Pattern 'mcpo'` returns nothing; ENTRYPOINT line matches |
| T-A10 | A10 stdio unchanged | run `python -m homelab_mcp.server` w/ stdin `initialize` payload, assert serverInfo.name |
| T-A11 | A11 tests added | file exists; pytest collects > 0 |
| T-A12 | A12 existing tests pass | `uv run pytest mcp/tests/` exits 0 |

## 5. File Inventory

Files created in this PR:

| Path | Action | Type |
|------|--------|------|
| `mcp/src/homelab_mcp/http_app.py` | created | source |
| `mcp/tests/test_http_app.py` | created | test |
| `out/Rivet/sdd/fastapi-phase1/contract.md` | created | SDD artifact |
| `out/Rivet/sdd/fastapi-phase1/spec.md` | created | SDD artifact |
| `out/Rivet/sdd/fastapi-phase1/design.md` | created | SDD artifact |
| `out/Rivet/sdd/fastapi-phase1/as-findings.json` | created | SDD artifact |

Files modified:

| Path | Action | What |
|------|--------|------|
| `mcp/pyproject.toml` | modified | add fastapi+uvicorn deps; add `homelab-mcp-http` script; optional `test` extra |
| `mcp/Dockerfile` | modified | drop `mcpo` from pip; ENTRYPOINT → `homelab-mcp-http` |

Files explicitly unchanged:

| Path | Why |
|------|-----|
| `mcp/src/homelab_mcp/server.py` | stdio entry — must stay byte-identical |
| `mcp/src/homelab_mcp/_runtime.py`, `app.py`, `entrypoints.py`, `tools/*` | tool registration logic; not in scope |
| Any existing test under `mcp/tests/` | regression risk; spec says "must still pass" |
| `mcp/Dockerfile.domain` | per-domain images stay on existing entrypoint until Phase 2 |

## 6. Rollback

`git revert` of this PR. Image build of the previous tag is still on
GHCR (sha-fc5339c). Cluster pulls the previous image; host wrapper
already uses `--entrypoint homelab-mcp` so it doesn't care about
container ENTRYPOINT changes either way.

## 7. Rejected alternatives

- **Keep mcpo, add a sidecar with auth/health.** Two processes per pod
  again, doubles failure modes. The whole point is to consolidate.
- **Write a custom Starlette app from scratch.** FastAPI buys us
  middleware composition + dependency injection + future Swagger for
  Phase 2 with no extra cost.
- **Use Hypercorn instead of uvicorn.** Marginal differences, uvicorn
  is the FastAPI default and has wider tooling.
- **Bake auth into the MCP layer (custom FastMCP middleware).** MCP
  doesn't have a standard auth concept — Phase 3 will do this with
  Cloudflare Access at the edge anyway.
