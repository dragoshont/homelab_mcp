"""Tests that prove the readonly-mode contract for all guarded mutating tools.

These tests refute two adversarial findings (Rivet-Verify ADV-008, run
20260424-165131Z) which inferred from a +diff alone that newly-guarded tools
were missing from `_WRITE_TOOLS`. In fact every guarded tool IS a member;
these tests assert membership and runtime enforcement so a regression cannot
re-introduce the false-positive condition.

RC-4: each gate test proves BOTH 'satisfied -> passes' and 'NOT satisfied
-> blocks with correct error message'.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest


# All tools that perform external state mutation. Must match _WRITE_TOOLS.
EXPECTED_WRITE_TOOLS = frozenset({
    "kube_restart", "flux_reconcile", "flux_suspend", "flux_resume",
    "qbt_pause", "qbt_resume",
    "dirigera_set_light", "dirigera_set_outlet", "dirigera_set_blind",
    "dirigera_trigger_scene",
    "apple_play_pause", "apple_volume", "apple_remote", "apple_launch_app",
    "apple_run_shortcut",
    "unifi_block", "unifi_unblock", "unifi_reconnect", "unifi_wlan_set",
    "kube_image_can_pull",
    "sonarr_search_missing", "radarr_search_missing",
    "lidarr_search_missing", "readarr_search_missing",
    "mylar3_search_missing",
    "plex_scan_library", "plex_maintenance",
    "prowlarr_add_torznab_indexer", "prowlarr_remove_indexer",
})


def _reload_server(readonly: bool):
    """Reload server module under a specific HOMELAB_MCP_READONLY value.

    `_READONLY` is read once at import time; we must reload for env to apply.
    """
    if readonly:
        os.environ["HOMELAB_MCP_READONLY"] = "true"
    else:
        os.environ.pop("HOMELAB_MCP_READONLY", None)
    sys.modules.pop("homelab_mcp.server", None)
    return importlib.import_module("homelab_mcp.server")


def test_write_tools_set_matches_expected():
    """_WRITE_TOOLS must match EXACTLY (no missing, no extras).

    Refutes Rivet-Verify ADV-008 false-positive about apple_volume / qbt_* /
    dirigera_* / apple_* membership AND prevents accidental over-blocking by
    catching read tools added to the set.
    """
    mod = _reload_server(readonly=False)
    missing = EXPECTED_WRITE_TOOLS - mod._WRITE_TOOLS
    extras = mod._WRITE_TOOLS - EXPECTED_WRITE_TOOLS
    assert not missing and not extras, (
        f"_WRITE_TOOLS mismatch. Missing: {sorted(missing)}. "
        f"Unexpected extras: {sorted(extras)}."
    )


@pytest.mark.parametrize("tool", sorted(EXPECTED_WRITE_TOOLS))
def test_check_readonly_blocks_when_enabled(tool):
    """In readonly mode, _check_readonly() raises with 'readonly mode'."""
    mod = _reload_server(readonly=True)
    with pytest.raises(RuntimeError, match="readonly mode"):
        mod._check_readonly(tool)


@pytest.mark.parametrize("tool", sorted(EXPECTED_WRITE_TOOLS))
def test_check_readonly_allows_when_disabled(tool):
    """When readonly is OFF, _check_readonly() is a no-op for write tools."""
    mod = _reload_server(readonly=False)
    # Should not raise.
    mod._check_readonly(tool)


def test_check_readonly_allows_unknown_tool_even_in_readonly():
    """Read-only tools (not in _WRITE_TOOLS) are allowed even in readonly mode."""
    mod = _reload_server(readonly=True)
    mod._check_readonly("radarr_movies")  # known read-only tool
    mod._check_readonly("not_a_real_tool")  # unknown tool: read-only by default


def teardown_module(_mod):
    """Restore non-readonly state for downstream tests."""
    os.environ.pop("HOMELAB_MCP_READONLY", None)
    sys.modules.pop("homelab_mcp.server", None)


# ---------------------------------------------------------------------------
# Entrypoint tests (ADV-007): prove each tool function ACTUALLY calls
# _check_readonly. Without these, removing a guard from a tool body would not
# fail any test (the unit tests above only exercise the primitive).
# ---------------------------------------------------------------------------

# Sync tools that can be invoked directly with simple stubbing.
SYNC_TOOL_INVOCATIONS = [
    ("sonarr_search_missing", lambda mod: mod.sonarr_search_missing(), {"_sonarr": lambda: None}),
    ("radarr_search_missing", lambda mod: mod.radarr_search_missing(), {"_radarr": lambda: None}),
    ("lidarr_search_missing", lambda mod: mod.lidarr_search_missing(), {"_lidarr": lambda: None}),
    ("readarr_search_missing", lambda mod: mod.readarr_search_missing(), {"_readarr": lambda: None}),
    ("mylar3_search_missing", lambda mod: mod.mylar3_search_missing(), {"_mylar3": lambda: None}),
    ("qbt_pause", lambda mod: mod.qbt_pause("all"), {"_qbt": lambda: None}),
    ("qbt_resume", lambda mod: mod.qbt_resume("all"), {"_qbt": lambda: None}),
    ("plex_scan_library", lambda mod: mod.plex_scan_library("1"), {"_plex": lambda: None}),
    ("plex_maintenance", lambda mod: mod.plex_maintenance(), {"_plex": lambda: None}),
    ("kube_image_can_pull", lambda mod: mod.kube_image_can_pull("nginx:latest"), {}),
    ("dirigera_set_light", lambda mod: mod.dirigera_set_light("x", on=True), {"_need_dirigera": lambda: None}),
    ("dirigera_set_outlet", lambda mod: mod.dirigera_set_outlet("x", True), {"_need_dirigera": lambda: None}),
    ("dirigera_set_blind", lambda mod: mod.dirigera_set_blind("x", 50), {"_need_dirigera": lambda: None}),
    ("dirigera_trigger_scene", lambda mod: mod.dirigera_trigger_scene("x"), {"_need_dirigera": lambda: None}),
    ("apple_play_pause", lambda mod: mod.apple_play_pause("tv"), {}),
    ("apple_volume_write", lambda mod: mod.apple_volume("tv", 50.0), {}),
    ("apple_remote", lambda mod: mod.apple_remote("tv", "menu"), {}),
    ("apple_launch_app", lambda mod: mod.apple_launch_app("tv", "netflix"), {}),
    ("apple_run_shortcut", lambda mod: mod.apple_run_shortcut("tv", "goodnight"), {}),
    ("unifi_block", lambda mod: mod.unifi_block("x"), {}),
    ("unifi_unblock", lambda mod: mod.unifi_unblock("x"), {}),
    ("unifi_reconnect", lambda mod: mod.unifi_reconnect("x"), {}),
    ("unifi_wlan_set", lambda mod: mod.unifi_wlan_set("ssid", True), {}),
    ("prowlarr_add_torznab_indexer",
     lambda mod: mod.prowlarr_add_torznab_indexer("name", "https://x.example/api", "abc123"),
     {"_prowlarr": lambda: None}),
    ("prowlarr_remove_indexer",
     lambda mod: mod.prowlarr_remove_indexer(1),
     {"_prowlarr": lambda: None}),
]


@pytest.mark.parametrize("label,invoke,patches", SYNC_TOOL_INVOCATIONS,
                         ids=[t[0] for t in SYNC_TOOL_INVOCATIONS])
def test_tool_entrypoint_blocks_in_readonly(label, invoke, patches, monkeypatch):
    """Calling the actual tool function (not the primitive) must raise in readonly.

    This catches the ADV-007 attack of removing `_check_readonly(...)` from a
    tool body — the primitive tests would still pass, but this entrypoint
    test would fail because the tool body would proceed past the guard and
    invoke the (stubbed) backend client.
    """
    mod = _reload_server(readonly=True)
    for name, value in patches.items():
        monkeypatch.setattr(mod, name, value)
    with pytest.raises(RuntimeError, match="readonly mode"):
        invoke(mod)


def test_apple_volume_read_path_does_not_block(monkeypatch):
    """apple_volume(level=None) is the documented READ path; must not raise.

    It will fail later (no real Apple TV) but must NOT raise the readonly
    RuntimeError. We assert the failure is something else (or no failure if
    the connection stub works).
    """
    mod = _reload_server(readonly=True)
    try:
        mod.apple_volume("tv")  # level omitted = read mode
    except RuntimeError as e:
        assert "readonly mode" not in str(e), (
            "Read-mode apple_volume must not be blocked by readonly guard"
        )
    except Exception:
        pass  # Any other exception (connection failure, etc.) is acceptable


def test_qbt_write_tools_use_qbittorrent_v5_endpoints(monkeypatch):
    mod = _reload_server(readonly=False)
    calls: list[tuple[str, dict]] = []

    class FakeQbt:
        def post(self, endpoint, data=None):
            calls.append((endpoint, data or {}))

    monkeypatch.setattr(mod, "_qbt", lambda: FakeQbt())
    mod.qbt_pause("hash-a")
    mod.qbt_resume("hash-b")

    assert calls == [
        ("/torrents/stop", {"hashes": "hash-a"}),
        ("/torrents/start", {"hashes": "hash-b"}),
    ]


# ---------------------------------------------------------------------------
# Audit-on-rejection (ADV-008 / SEA-003): blocked write attempts must still
# produce an audit log entry so admins can see denied mutation attempts.
# ---------------------------------------------------------------------------

def test_blocked_write_is_still_audited(monkeypatch):
    """When readonly blocks a write, the attempt MUST be audit-logged.

    AUD-1 contract: blocked calls produce TWO audit entries:
      1. Optimistic pre-audit from the tool body (status defaults to "ok")
      2. Rejection audit from _check_readonly (status="rejected_readonly")
    The rejection entry is what proves the gate fired.
    """
    mod = _reload_server(readonly=True)
    audited: list[tuple] = []
    monkeypatch.setattr(
        mod, "_audit",
        lambda name, params, status="ok": audited.append((name, params, status)),
    )
    with pytest.raises(RuntimeError, match="readonly mode"):
        mod.qbt_pause("all")
    assert len(audited) >= 1, "Blocked mutation must produce at least one audit entry"
    rejections = [e for e in audited if e[2] == "rejected_readonly"]
    assert len(rejections) == 1, f"Expected exactly one rejection audit, got: {audited}"
    assert rejections[0][0] == "qbt_pause"
