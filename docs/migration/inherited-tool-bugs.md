# Inherited tool-implementation bugs (tracked for Phase 1+)

These bugs in the lifted MCP source code were surfaced by `rivet verify`
during Phase 0.5 (Step 9 adversarial review, 2026-05-01) but **deliberately
not fixed in the Phase 0.5 PR** because Phase 0.5's contract MP-2 is
"no code changes — byte-faithful lift". Fixing them in `homelab_mcp/mcp/`
would create drift between source and lifted code.

Each will be addressed in the appropriate Phase 1+ split-server SDD when
its containing tool is moved into a domain-scoped package.

## High severity

| ID | Tool / file | Symptom | Fix sketch |
|----|-------------|---------|------------|
| BUG-001-httpx-lifecycle | `clients.py` (Servarr/Qbt/Mylar3/Plex/Homebridge) | `httpx.Client` instances created per service, never `.close()`d. Long-running pods accumulate sockets. | Wrap clients in context-managed accessor or refactor to module-level singletons with explicit shutdown hook. |
| BUG-003-apple-bound-method | `server.py:apple_now_playing` | `p = atv.metadata.playing` captures the bound method without calling it. `hasattr(p, '__await__')` is False so the method is never invoked; all metadata returns None. | `p = await atv.metadata.playing()` and remove the awaitable-detection branch. |
| BUG-004-qbt-200-fail | `clients.py:QbtClient._login` | qBittorrent returns HTTP 200 with body `Fails.` on bad credentials. `raise_for_status()` passes; `_logged_in=True` is set; subsequent calls hit a 403 retry loop. | Inspect response body; raise on `Fails.`. |
| BUG-005-audit-ordering | `server.py` ~15 write tools | `_audit(tool, params)` is called BEFORE `_check_readonly(tool)`, so a blocked write produces a misleading `ok` audit entry followed by `rejected_readonly`. | Reverse the order or have `_check_readonly` cancel the prior audit entry. |
| ADV-002-cf-pagination | `server.py:cf_dns_list` | Single `per_page=200` call; zones with >200 records lose data silently. | Loop on `result_info.next_page` until exhausted. |
| ADV-004-unifi-port (FIXED 2026-05-01) | `clients.py:get_unifi_config` | ~~Empty `UNIFI_PORT` crashes startup with `int('')`~~. | ~~Coalesce empty to default~~. |
| ADV-005-dirigera-init-cache | `server.py` dirigera client init | First-call exception caches `None` in `_clients['dirigera']`; subsequent calls never retry. | Don't cache `None`; re-raise or attempt re-init on each call. |
| ADV-008-kube-image-can-pull-ready | `server.py:kube_image_can_pull` | Test pod uses `/bin/true` and exits immediately; `condition=Ready` never satisfied because pod transitions Pending→Succeeded. | Use `condition=PodReadyToStartContainers` or check `phase=Succeeded` instead. |

## Medium severity

| ID | Tool / file | Symptom | Fix sketch |
|----|-------------|---------|------------|
| BUG-005-clients-race | `server.py:_clients` dict | Concurrent tool calls can both miss the cache check and create duplicate clients. GIL prevents corruption, but produces inconsistent auth state. | `threading.Lock` around the dict, or initialize all clients eagerly at import. |
| ADV-002-image-list-tags-limit-zero | `server.py:image_list_tags` | `tags[-0:]` returns ALL tags instead of zero. | Validate `limit >= 1` or special-case 0. |
| ADV-004-host-status-mem-parse | `server.py:host_status` | If `MEM:` value is empty (SSH partial failure), `int('')` raises `ValueError`. | `try/except ValueError` returning `{"mem_error": ...}`. |
| ADV-004-homebridge-no-token | `clients.py:HomebridgeClient._login` | Login response missing `access_token` key sets `self._token = None`; subsequent `Authorization: Bearer None` header fails opaquely. | Reject missing token at login with clear error. |
| ADV-004-kube-image-present-no-crictl | `server.py:kube_image_present` | Fallback `|| echo '[]'` returns a JSON array, but code unconditionally calls `data.get(...)`. | Type-check before `.get`. |
| ADV-008-check-readonly-signature | `policy.py:check_readonly` | Type annotation says `Callable[[str, dict, str], None]` but `audit.audit` requires Logger as first arg. Direct passing raises TypeError. | Either bind logger via partial in server.py (current behaviour) or update annotation to match. |

## What "addressed in Phase 1+" means

When the platform/media/network/homeauto/control split server SDDs ship,
each MUST:

1. Identify which of the bugs above touches a tool in its domain.
2. Fix the bug as part of the split (not the lift), with a regression test.
3. Mark the row above as `(FIXED <commit>)` in this file.

This file is the canonical to-do list for inherited code-quality work.
