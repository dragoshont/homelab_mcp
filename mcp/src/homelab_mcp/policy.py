"""Read-only policy for mutating homelab MCP tools."""

from __future__ import annotations

from collections.abc import Callable


WRITE_TOOLS = frozenset({
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


def check_readonly(
    tool_name: str,
    *,
    readonly: bool,
    audit: Callable[[str, dict, str], None],
) -> None:
    """Raise if a mutating tool is blocked by read-only mode."""
    if readonly and tool_name in WRITE_TOOLS:
        audit(tool_name, {}, "rejected_readonly")
        raise RuntimeError(
            f"Tool '{tool_name}' is disabled \u2014 server is in readonly mode "
            f"(HOMELAB_MCP_READONLY=true). Unset to enable writes."
        )