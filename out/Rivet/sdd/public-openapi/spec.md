# Spec — public-openapi

## Problem

OpenWebUI's tool-server importer issues an unauthenticated `GET /openapi.json` and skips entries that return non-2xx. Phase 1's blanket bearer middleware blocks this discovery, so the homelab tool server doesn't appear in OpenWebUI's chat picker even though the entry is correctly registered in OpenWebUI's `tool_server.connections` table. Confirmed with live cluster trace:

```text
$ kubectl exec openwebui-open-webui-0 -c open-webui -- \
    curl -s -o /dev/null -w "%{http_code}\n" \
    http://homelab-mcp-proxy.default.svc.cluster.local:8080/openapi.json
401
```

## User-capability sentence (RC-15)

After this wave, the user can: open chat.hont.ro, send a chat message, and the model successfully invokes a homelab MCP tool (e.g. `host_status`) — verified by both (a) the tool server appearing in OpenWebUI's tool list and (b) a real tool invocation returning live cluster data.

## Acceptance criteria (measurable)

| ID | Criterion | Measurement |
|----|-----------|-------------|
| AC-1 | `/openapi.json` returns 200 unauthenticated when token is set | `curl -sf http://homelab-mcp-proxy:8080/openapi.json` exits 0 |
| AC-2 | `/openapi.json/` (trailing slash) also returns 200 | same as AC-1 with trailing slash |
| AC-3 | Doc body parses as valid OpenAPI 3.1 with ≥1 path | `jq '.openapi == "3.1.0" and (.paths | length) > 0'` returns true |
| AC-4 | `POST /<tool>` without bearer returns 401 | `curl -X POST .../host_status` returns 401 |
| AC-5 | `POST /<tool>` with correct bearer returns 200 | same with `Authorization: Bearer $TOKEN` |
| AC-6 | `/mcp` without bearer returns 401 | unchanged from Phase 1 |
| AC-7 | OpenWebUI's tool-server discovery succeeds for the homelab connection | After pod rollout: `kubectl exec openwebui-open-webui-0 -c open-webui -- python3 -c "import sqlite3, json; c = sqlite3.connect('/app/backend/data/webui.db'); cfg = json.loads(c.execute('SELECT data FROM config WHERE id=1').fetchone()[0]); ts = next(t for t in cfg['tool_server']['connections'] if t['info']['name'] == 'homelab'); print('enable=', t['config']['enable'])"` returns `enable= True` AND no `last_error` field set on the entry. |
| AC-8 | End-to-end: chat invokes a homelab tool and returns real data | `kubectl exec openwebui-open-webui-0 -c open-webui -- curl -sf -H "Authorization: Bearer $OWUI_KEY" -H "Content-Type: application/json" -d '{"model":"gemini-2.5-flash","messages":[{"role":"user","content":"Call the host_status tool."}],"tools_required":["host_status"]}' http://localhost:8080/api/chat/completions \| jq '.choices[0].message.tool_calls[0].function.name'` returns `"host_status"` AND a follow-up `tool` message contains a non-error JSON body with `os_version`. |
| AC-9 | All existing tests in `mcp/tests/test_http_app.py` pass | `pytest -q` exits 0 |
| AC-10 | New tests covering MF-7 (path confusion) and MF-8 (method confusion) added and pass | grep test file for the test names |
| AC-11 | Path-traversal payload `/openapi.json/../mcp` does NOT bypass auth | `curl -sf -o /dev/null -w "%{http_code}" .../%2E%2E/mcp` returns 401 |
| AC-12 | Public-path bypass list documented in code with attribution to this SDD | `grep -A3 "public-openapi"` in `http_app.py` |
| AC-13 | HEAD `/openapi.json` returns 200 (or whatever Starlette dispatches for HEAD-on-GET) without auth | `curl -sI -o /dev/null -w "%{http_code}" http://homelab-mcp-proxy:8080/openapi.json` returns 200 |
| AC-14 | `/openapi.json` cached doc serialisation completes in p99 < 10 ms on the deployed SKU (1 vCPU, 2 GB RAM, ARM64 single-node) | Smoke loop: `for i in $(seq 1 100); do time curl -sf -o /dev/null http://homelab-mcp-proxy:8080/openapi.json; done` — sorted real-times, 99th percentile < 0.010s |

## Non-goals

- HMAC / signed-URL auth for `/openapi.json` discovery.
- Adding Swagger UI (`/docs`).
- IP allow-listing (NetworkPolicy concern).
- Public ingress (Phase 3 work).
