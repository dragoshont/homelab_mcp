"""Homelab MCP Server — all-in-one MCP for every homelab service."""

from __future__ import annotations

import os
import subprocess

from homelab_mcp.app import create_mcp
from homelab_mcp.audit import audit as _write_audit
from homelab_mcp.audit import configure_audit_logger
from homelab_mcp.clients import (
    ServarrClient,
    QbtClient,
    PlexClient,
    HomebridgeClient,
    Mylar3Client,
    get_sonarr_config,
    get_radarr_config,
    get_lidarr_config,
    get_readarr_config,
    get_mylar3_config,
    get_prowlarr_config,
    get_qbt_config,
    get_plex_config,
    get_homebridge_config,
    get_scrypted_config,
    get_dirigera_config,
    get_unifi_config,
    env,
)
from homelab_mcp.policy import WRITE_TOOLS as _POLICY_WRITE_TOOLS
from homelab_mcp.policy import check_readonly as _policy_check_readonly
from homelab_mcp.settings import (
    audit_log_path,
    cf_allowed_zones,
    cf_default_zone,
    env_flag,
    homelab_ingress_ip,
    ingress_host_re,
    require_env,
)

mcp = create_mcp()

# Lazy-initialized clients (created on first tool call, not at import time)
_clients: dict = {}

# ============================================================
# AUDIT LOGGING (Phase 16)
# ============================================================

_AUDIT_LOG_PATH = audit_log_path()
_audit_logger = configure_audit_logger(_AUDIT_LOG_PATH)


def _audit(tool_name: str, params: dict, result_summary: str = "ok") -> None:
    """Compatibility wrapper for the extracted audit helper."""
    _write_audit(_audit_logger, tool_name, params, result_summary)


# ============================================================
# READONLY MODE (Phase 16)
# ============================================================

_READONLY = env_flag("HOMELAB_MCP_READONLY")

# Tools that mutate state
_WRITE_TOOLS = _POLICY_WRITE_TOOLS


def _check_readonly(tool_name: str) -> None:
    """Raise if the server is in readonly mode and the tool mutates.

    Emits a `rejected_readonly` audit line before raising so the operator can
    distinguish blocked attempts from successful calls in audit.log.
    """
    _policy_check_readonly(tool_name, readonly=_READONLY, audit=_audit)


def _sonarr() -> ServarrClient:
    if "sonarr" not in _clients:
        _clients["sonarr"] = ServarrClient(get_sonarr_config(), api_version="v3")
    return _clients["sonarr"]


def _radarr() -> ServarrClient:
    if "radarr" not in _clients:
        _clients["radarr"] = ServarrClient(get_radarr_config(), api_version="v3")
    return _clients["radarr"]


def _lidarr() -> ServarrClient:
    if "lidarr" not in _clients:
        _clients["lidarr"] = ServarrClient(get_lidarr_config(), api_version="v1")
    return _clients["lidarr"]


def _readarr() -> ServarrClient:
    if "readarr" not in _clients:
        _clients["readarr"] = ServarrClient(get_readarr_config(), api_version="v1")
    return _clients["readarr"]


def _mylar3() -> Mylar3Client:
    if "mylar3" not in _clients:
        _clients["mylar3"] = Mylar3Client(get_mylar3_config())
    return _clients["mylar3"]


def _prowlarr() -> ServarrClient:
    if "prowlarr" not in _clients:
        _clients["prowlarr"] = ServarrClient(get_prowlarr_config(), api_version="v1")
    return _clients["prowlarr"]


def _qbt() -> QbtClient:
    if "qbt" not in _clients:
        _clients["qbt"] = QbtClient(get_qbt_config())
    return _clients["qbt"]


def _plex() -> PlexClient:
    if "plex" not in _clients:
        _clients["plex"] = PlexClient(get_plex_config())
    return _clients["plex"]


def _homebridge() -> HomebridgeClient:
    if "homebridge" not in _clients:
        _clients["homebridge"] = HomebridgeClient(get_homebridge_config())
    return _clients["homebridge"]


# ============================================================
# SONARR TOOLS
# ============================================================


@mcp.tool()
def sonarr_health() -> dict:
    """Check Sonarr health status, version, and disk space."""
    c = _sonarr()
    health = c.get("/health")
    status = c.get("/system/status")
    disk = c.get("/diskspace")
    return {
        "version": status.get("version", "unknown"),
        "health_issues": len(health),
        "health": health,
        "diskspace": disk,
    }


@mcp.tool()
def sonarr_missing(page_size: int = 50) -> dict:
    """List missing episodes from Sonarr."""
    c = _sonarr()
    data = c.get("/wanted/missing", pageSize=page_size, sortKey="airDateUtc",
                 sortDirection="descending", includeSeries="true")
    return {
        "total_missing": data["totalRecords"],
        "episodes": [
            {"series": r["series"]["title"], "season": r["seasonNumber"],
             "episode": r["episodeNumber"], "title": r["title"],
             "airDate": r.get("airDateUtc")}
            for r in data.get("records", [])
        ],
    }


@mcp.tool()
def sonarr_queue() -> dict:
    """Show Sonarr download queue with progress and errors."""
    c = _sonarr()
    data = c.get("/queue", pageSize=50, includeSeries="true", includeEpisode="true")
    return {
        "total_queued": data["totalRecords"],
        "items": [
            {"title": r.get("title"), "status": r.get("status"),
             "size": r.get("size"), "sizeleft": r.get("sizeleft"),
             "series": r.get("series", {}).get("title"),
             "episode": r.get("episode", {}).get("title"),
             "error": r.get("errorMessage")}
            for r in data.get("records", [])
        ],
    }


@mcp.tool()
def sonarr_calendar(days: int = 7) -> dict:
    """Get upcoming episodes from Sonarr for the next N days."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%dT00:00:00Z")
    end = (now + timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    c = _sonarr()
    data = c.get("/calendar", start=start, end=end, includeSeries="true")
    return {
        "upcoming_count": len(data),
        "episodes": [
            {"series": ep.get("series", {}).get("title"),
             "season": ep.get("seasonNumber"), "episode": ep.get("episodeNumber"),
             "title": ep.get("title"), "airDate": ep.get("airDateUtc"),
             "hasFile": ep.get("hasFile")}
            for ep in data
        ],
    }


@mcp.tool()
def sonarr_series() -> dict:
    """List all TV series in Sonarr with monitoring status."""
    c = _sonarr()
    series = c.get("/series")
    return {
        "count": len(series),
        "series": [
            {"title": s["title"], "year": s.get("year"),
             "monitored": s["monitored"], "status": s.get("status"),
             "episodeCount": s.get("statistics", {}).get("episodeCount", 0),
             "episodeFileCount": s.get("statistics", {}).get("episodeFileCount", 0),
             "sizeOnDisk": s.get("statistics", {}).get("sizeOnDisk", 0)}
            for s in series
        ],
    }


@mcp.tool()
def sonarr_search_missing() -> dict:
    """Trigger a search for all missing episodes in Sonarr."""
    _audit("sonarr_search_missing", {})
    _check_readonly("sonarr_search_missing")
    c = _sonarr()
    result = c.post("/command", json={"name": "MissingEpisodeSearch"})
    return {"command_id": result.get("id"), "status": result.get("status")}


# ============================================================
# RADARR TOOLS
# ============================================================


@mcp.tool()
def radarr_health() -> dict:
    """Check Radarr health status, version, and disk space."""
    c = _radarr()
    health = c.get("/health")
    status = c.get("/system/status")
    disk = c.get("/diskspace")
    return {
        "version": status.get("version", "unknown"),
        "health_issues": len(health),
        "health": health,
        "diskspace": disk,
    }


@mcp.tool()
def radarr_missing(page_size: int = 50) -> dict:
    """List movies missing files in Radarr."""
    c = _radarr()
    data = c.get("/wanted/missing", pageSize=page_size)
    return {
        "total_missing": data["totalRecords"],
        "movies": [
            {"title": r["title"], "year": r.get("year"),
             "monitored": r["monitored"]}
            for r in data.get("records", [])
        ],
    }


@mcp.tool()
def radarr_queue() -> dict:
    """Show Radarr download queue."""
    c = _radarr()
    data = c.get("/queue", pageSize=50)
    return {
        "total_queued": data["totalRecords"],
        "items": [
            {"title": r.get("title"), "status": r.get("status"),
             "size": r.get("size"), "sizeleft": r.get("sizeleft"),
             "error": r.get("errorMessage")}
            for r in data.get("records", [])
        ],
    }


@mcp.tool()
def radarr_movies() -> dict:
    """List all movies in Radarr with file status."""
    c = _radarr()
    movies = c.get("/movie")
    return {
        "count": len(movies),
        "movies": [
            {"title": m["title"], "year": m.get("year"),
             "monitored": m["monitored"], "hasFile": m.get("hasFile"),
             "sizeOnDisk": m.get("sizeOnDisk", 0)}
            for m in movies
        ],
    }


@mcp.tool()
def radarr_calendar(days: int = 30) -> dict:
    """Get upcoming movie releases from Radarr."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%dT00:00:00Z")
    end = (now + timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    c = _radarr()
    data = c.get("/calendar", start=start, end=end)
    return {
        "upcoming_count": len(data),
        "movies": [
            {"title": m.get("title"), "year": m.get("year"),
             "releaseDate": m.get("digitalRelease") or m.get("physicalRelease")}
            for m in data
        ],
    }


@mcp.tool()
def radarr_search_missing() -> dict:
    """Trigger a search for all missing movies in Radarr."""
    _audit("radarr_search_missing", {})
    _check_readonly("radarr_search_missing")
    c = _radarr()
    result = c.post("/command", json={"name": "MissingMoviesSearch"})
    return {"command_id": result.get("id"), "status": result.get("status")}


# ============================================================
# LIDARR TOOLS (music)
# ============================================================


@mcp.tool()
def lidarr_health() -> dict:
    """Check Lidarr health status, version, and disk space."""
    c = _lidarr()
    health = c.get("/health")
    status = c.get("/system/status")
    disk = c.get("/diskspace")
    return {
        "version": status.get("version", "unknown"),
        "health_issues": len(health),
        "health": health,
        "diskspace": disk,
    }


@mcp.tool()
def lidarr_missing(page_size: int = 50) -> dict:
    """List missing albums from Lidarr (monitored artists with no file)."""
    c = _lidarr()
    data = c.get("/wanted/missing", pageSize=page_size, sortKey="releaseDate",
                 sortDirection="descending", includeArtist="true")
    return {
        "total_missing": data["totalRecords"],
        "albums": [
            {"artist": r.get("artist", {}).get("artistName"),
             "title": r.get("title"),
             "releaseDate": r.get("releaseDate"),
             "monitored": r.get("monitored")}
            for r in data.get("records", [])
        ],
    }


@mcp.tool()
def lidarr_search_missing() -> dict:
    """Trigger a search for all missing albums in Lidarr."""
    _audit("lidarr_search_missing", {})
    _check_readonly("lidarr_search_missing")
    c = _lidarr()
    result = c.post("/command", json={"name": "MissingAlbumSearch"})
    return {"command_id": result.get("id"), "status": result.get("status")}


# ============================================================
# READARR TOOLS (books)
# ============================================================


@mcp.tool()
def readarr_health() -> dict:
    """Check Readarr health status, version, and disk space."""
    c = _readarr()
    health = c.get("/health")
    status = c.get("/system/status")
    disk = c.get("/diskspace")
    return {
        "version": status.get("version", "unknown"),
        "health_issues": len(health),
        "health": health,
        "diskspace": disk,
    }


@mcp.tool()
def readarr_missing(page_size: int = 50) -> dict:
    """List missing books from Readarr (monitored authors with no file)."""
    c = _readarr()
    data = c.get("/wanted/missing", pageSize=page_size, sortKey="releaseDate",
                 sortDirection="descending", includeAuthor="true")
    return {
        "total_missing": data["totalRecords"],
        "books": [
            {"author": r.get("author", {}).get("authorName"),
             "title": r.get("title"),
             "releaseDate": r.get("releaseDate"),
             "monitored": r.get("monitored")}
            for r in data.get("records", [])
        ],
    }


@mcp.tool()
def readarr_search_missing() -> dict:
    """Trigger a search for all missing books in Readarr."""
    _audit("readarr_search_missing", {})
    _check_readonly("readarr_search_missing")
    c = _readarr()
    result = c.post("/command", json={"name": "MissingBookSearch"})
    return {"command_id": result.get("id"), "status": result.get("status")}


# ============================================================
# MYLAR3 TOOLS (comics)
# ============================================================


@mcp.tool()
def mylar3_health() -> dict:
    """Check Mylar3 version and update status."""
    c = _mylar3()
    res = c.call("getVersion")
    data = res.get("data", {}) if isinstance(res, dict) else {}
    return {
        "current_version": data.get("current_version"),
        "latest_version": data.get("latest_version"),
        "commits_behind": data.get("commits_behind"),
        "install_type": data.get("install_type"),
    }


@mcp.tool()
def mylar3_series() -> dict:
    """List all comic series tracked by Mylar3 (the library index)."""
    c = _mylar3()
    res = c.call("getIndex")
    series = res.get("data", []) if isinstance(res, dict) else []
    return {
        "total_series": len(series),
        "series": [
            {"name": s.get("name") or s.get("ComicName"),
             "year": s.get("year") or s.get("ComicYear"),
             "publisher": s.get("publisher") or s.get("ComicPublisher"),
             "status": s.get("status") or s.get("Status"),
             "have": s.get("have") or s.get("Have"),
             "total": s.get("total") or s.get("Total")}
            for s in series
        ],
    }


@mcp.tool()
def mylar3_missing() -> dict:
    """List wanted (missing) comic issues across all monitored series."""
    c = _mylar3()
    res = c.call("getWanted")
    issues = res.get("issues", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
    return {
        "total_missing": len(issues),
        "issues": [
            {"series": i.get("ComicName"),
             "issue": i.get("Issue_Number"),
             "year": i.get("IssueYear"),
             "status": i.get("Status")}
            for i in issues
        ],
    }


@mcp.tool()
def mylar3_search_missing() -> dict:
    """Trigger Mylar3's force-search across all wanted issues."""
    _audit("mylar3_search_missing", {})
    _check_readonly("mylar3_search_missing")
    c = _mylar3()
    res = c.call("forceSearch")
    return {"result": res if isinstance(res, str) else res.get("data", res)}


# ============================================================
# PROWLARR TOOLS
# ============================================================


@mcp.tool()
def prowlarr_health() -> dict:
    """Check Prowlarr health status, version, and indexer counts."""
    c = _prowlarr()
    health = c.get("/health")
    status = c.get("/system/status")
    indexers = c.get("/indexer")
    enabled = [i for i in indexers if i.get("enable")]
    return {
        "version": status.get("version", "unknown"),
        "health_issues": len(health),
        "health": health,
        "indexers_total": len(indexers),
        "indexers_enabled": len(enabled),
    }


@mcp.tool()
def prowlarr_test_indexers() -> dict:
    """Test all configured indexers in Prowlarr."""
    c = _prowlarr()
    c.post("/indexer/testall")
    indexers = c.get("/indexer")
    return {
        "tested": len(indexers),
        "indexers": [
            {"name": i["name"], "enabled": i.get("enable"),
             "protocol": i.get("protocol")}
            for i in indexers
        ],
    }


@mcp.tool()
def prowlarr_search(query: str) -> dict:
    """Search across all Prowlarr indexers."""
    c = _prowlarr()
    results = c.get("/search", query=query)
    return {
        "result_count": len(results),
        "results": [
            {"title": r.get("title"), "indexer": r.get("indexer"),
             "size": r.get("size"), "seeders": r.get("seeders")}
            for r in results[:20]
        ],
    }


# ============================================================
# QBITTORRENT TOOLS
# ============================================================


@mcp.tool()
def qbt_status() -> dict:
    """Get qBittorrent transfer info and torrent summary counts."""
    c = _qbt()
    transfer = c.get("/transfer/info")
    torrents = c.get("/torrents/info")
    states = {}
    for t in torrents:
        s = t.get("state", "unknown")
        states[s] = states.get(s, 0) + 1
    return {
        "dl_speed_bytes": transfer.get("dl_info_speed", 0),
        "ul_speed_bytes": transfer.get("up_info_speed", 0),
        "connection": transfer.get("connection_status", "unknown"),
        "torrents_total": len(torrents),
        "by_state": states,
    }


@mcp.tool()
def qbt_torrents(filter: str = "all") -> dict:
    """List qBittorrent torrents. Filter: all|downloading|seeding|completed|paused|active|stalled|errored."""
    c = _qbt()
    data = c.get("/torrents/info", filter=filter)
    return {
        "filter": filter,
        "count": len(data),
        "torrents": [
            {"name": t["name"], "state": t["state"], "progress": t["progress"],
             "size": t["size"], "dlspeed": t["dlspeed"], "upspeed": t["upspeed"],
             "ratio": t["ratio"], "category": t.get("category", ""),
             "tags": t.get("tags", "")}
            for t in data[:50]
        ],
    }


@mcp.tool()
def qbt_pause(hashes: str = "all") -> dict:
    """Pause torrent(s). Pass hash(es) separated by | or 'all'."""
    _audit("qbt_pause", {"hashes": hashes})
    _check_readonly("qbt_pause")
    c = _qbt()
    c.post("/torrents/stop", data={"hashes": hashes})
    return {"paused": hashes}


@mcp.tool()
def qbt_resume(hashes: str = "all") -> dict:
    """Resume torrent(s). Pass hash(es) separated by | or 'all'."""
    _audit("qbt_resume", {"hashes": hashes})
    _check_readonly("qbt_resume")
    c = _qbt()
    c.post("/torrents/start", data={"hashes": hashes})
    return {"resumed": hashes}


# ============================================================
# PLEX TOOLS
# ============================================================


@mcp.tool()
def plex_status() -> dict:
    """Get Plex server identity and active streaming sessions."""
    c = _plex()
    identity = c.get("/")
    mc = identity.get("MediaContainer", {})
    sessions = c.get("/status/sessions")
    sc = sessions.get("MediaContainer", {})
    return {
        "server_name": mc.get("friendlyName", "unknown"),
        "version": mc.get("version", "unknown"),
        "platform": mc.get("platform", "unknown"),
        "active_sessions": sc.get("size", 0),
        "streams": [
            {"user": s.get("User", {}).get("title"),
             "title": s.get("title"),
             "type": s.get("type"),
             "player": s.get("Player", {}).get("title"),
             "state": s.get("Player", {}).get("state")}
            for s in sc.get("Metadata", [])
        ],
    }


@mcp.tool()
def plex_libraries() -> dict:
    """List all Plex libraries with type and refresh status."""
    c = _plex()
    data = c.get("/library/sections")
    mc = data.get("MediaContainer", {})
    return {
        "library_count": mc.get("size", 0),
        "libraries": [
            {"key": lib["key"], "title": lib["title"], "type": lib["type"],
             "refreshing": lib.get("refreshing", False)}
            for lib in mc.get("Directory", [])
        ],
    }


@mcp.tool()
def plex_recent(count: int = 20) -> dict:
    """Get recently added media from Plex."""
    c = _plex()
    data = c.get("/library/recentlyAdded")
    mc = data.get("MediaContainer", {})
    items = mc.get("Metadata", [])[:count]
    return {
        "total_recent": mc.get("size", 0),
        "items": [
            {"title": i.get("title"), "type": i.get("type"),
             "year": i.get("year"), "addedAt": i.get("addedAt"),
             "parentTitle": i.get("parentTitle"),
             "grandparentTitle": i.get("grandparentTitle")}
            for i in items
        ],
    }


@mcp.tool()
def plex_scan_library(library_key: str) -> dict:
    """Trigger a library scan in Plex. Pass the library key (from plex_libraries)."""
    _audit("plex_scan_library", {"library_key": library_key})
    _check_readonly("plex_scan_library")
    c = _plex()
    c.put(f"/library/sections/{library_key}/refresh")
    return {"scanning": library_key}


@mcp.tool()
def plex_maintenance() -> dict:
    """Run Plex maintenance: optimize database, clean bundles, empty trash."""
    _audit("plex_maintenance", {})
    _check_readonly("plex_maintenance")
    c = _plex()
    c.put("/library/optimize")
    c.put("/library/clean/bundles")
    # empty trash per library
    libs = c.get("/library/sections")
    for lib in libs.get("MediaContainer", {}).get("Directory", []):
        c.put(f"/library/sections/{lib['key']}/emptyTrash")
    return {"optimized": True, "cleaned_bundles": True, "emptied_trash": True}


# ============================================================
# HOMEBRIDGE TOOLS
# ============================================================


@mcp.tool()
def homebridge_status() -> dict:
    """Get Homebridge status, version, and plugin update info."""
    c = _homebridge()
    hb = c.get("/status/homebridge")
    plugins = c.get("/plugins")
    updates = [p for p in plugins if p.get("updateAvailable")]
    return {
        "status": hb.get("status", "unknown"),
        "version": hb.get("homebridgeVersion", "unknown"),
        "plugins_installed": len(plugins),
        "plugins_update_available": len(updates),
        "updates": [{"name": p.get("name"), "installed": p.get("installedVersion"),
                      "latest": p.get("latestVersion")} for p in updates],
    }


@mcp.tool()
def homebridge_accessories() -> dict:
    """List all Homebridge accessories with their current status."""
    c = _homebridge()
    accessories = c.get("/accessories")
    return {
        "count": len(accessories),
        "accessories": [
            {"name": a.get("serviceName", a.get("accessoryInformation", {}).get("Name")),
             "type": a.get("type"),
             "uniqueId": a.get("uniqueId")}
            for a in accessories
        ],
    }


@mcp.tool()
def homebridge_plugins() -> dict:
    """List installed Homebridge plugins."""
    c = _homebridge()
    plugins = c.get("/plugins")
    return {
        "count": len(plugins),
        "plugins": [
            {"name": p.get("name"), "version": p.get("installedVersion"),
             "updateAvailable": p.get("updateAvailable", False)}
            for p in plugins
        ],
    }


# ============================================================
# SCRYPTED TOOLS
# ============================================================


@mcp.tool()
def scrypted_status() -> dict:
    """Check if Scrypted is reachable (health probe only)."""
    import httpx
    cfg = get_scrypted_config()
    try:
        r = httpx.get(cfg.url, timeout=10, follow_redirects=True)
        return {"reachable": r.status_code in (200, 301, 302),
                "http_code": r.status_code}
    except httpx.RequestError as e:
        return {"reachable": False, "error": str(e)}


# ============================================================
# UBUNTU HOST TOOLS (via SSH)
# ============================================================


def _ssh_exec(command: str, timeout: int = 60) -> str:
    """Execute a command on the homelab host via SSH."""
    host = require_env(
        "HOMELAB_HOST",
        hint="set to your homelab SSH host (no default; the server is meant to be deployment-agnostic)",
    )
    user = require_env(
        "HOMELAB_SSH_USER",
        hint="set to the SSH user on HOMELAB_HOST",
    )
    key_path = env("HOMELAB_SSH_KEY", "")

    ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new",
               "-o", "ConnectTimeout=10"]
    if key_path:
        ssh_cmd.extend(["-i", key_path])
    ssh_cmd.extend([f"{user}@{host}", command])

    result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"SSH failed: {result.stderr.strip()}")
    return result.stdout.strip()


@mcp.tool()
def host_status() -> dict:
    """Get Ubuntu host system status: uptime, load, disk, memory, k3s, packages, reboot status, OS version."""
    cmd = (
        "echo UPTIME:$(uptime -s); "
        "echo LOAD:$(cat /proc/loadavg | cut -d' ' -f1-3); "
        "echo DISK:$(df -h / /media/nas 2>/dev/null | tail -n+2 | awk '{print $1,$2,$3,$4,$5,$6}' | paste -sd'|'); "
        "echo MEM:$(free -m | awk '/^Mem:/{print $2,$3,$4}'); "
        "echo K3S:$(systemctl is-active k3s 2>/dev/null || echo unknown); "
        "echo UPDATES:$(apt list --upgradable 2>/dev/null | grep -c upgradable || echo 0); "
        "echo REBOOT:$(test -f /var/run/reboot-required && echo yes || echo no); "
        "echo OS:$(lsb_release -ds 2>/dev/null); "
        "echo KERNEL:$(uname -r)"
    )
    output = _ssh_exec(cmd)
    parsed = {}
    for line in output.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            parsed[key.strip()] = val.strip()

    mem_parts = parsed.get("MEM", "0 0 0").split()
    return {
        "uptime_since": parsed.get("UPTIME", ""),
        "load_avg": parsed.get("LOAD", ""),
        "disk": parsed.get("DISK", ""),
        "memory_mb": {"total": int(mem_parts[0]) if mem_parts else 0,
                       "used": int(mem_parts[1]) if len(mem_parts) > 1 else 0},
        "k3s_status": parsed.get("K3S", "unknown"),
        "packages_upgradable": int(parsed.get("UPDATES", "0") or "0"),
        "reboot_required": parsed.get("REBOOT", "no") == "yes",
        "os_version": parsed.get("OS", ""),
        "kernel": parsed.get("KERNEL", ""),
    }


@mcp.tool()
def host_disk() -> dict:
    """Get disk usage for all mount points on the Ubuntu host."""
    output = _ssh_exec("df -h | tail -n+2")
    mounts = []
    for line in output.split("\n"):
        parts = line.split()
        if len(parts) >= 6:
            mounts.append({
                "filesystem": parts[0], "size": parts[1], "used": parts[2],
                "available": parts[3], "use_pct": parts[4], "mount": parts[5],
            })
    return {"mounts": mounts}


@mcp.tool()
def host_services() -> dict:
    """Check status of key services on the Ubuntu host."""
    services = ["k3s", "ssh", "docker", "containerd"]
    output = _ssh_exec(
        " ; ".join(f'echo "{s}:$(systemctl is-active {s} 2>/dev/null || echo unknown)"'
                    for s in services)
    )
    result = {}
    for line in output.split("\n"):
        if ":" in line:
            name, _, status = line.partition(":")
            result[name.strip()] = status.strip()
    return {"services": result}


@mcp.tool()
def host_packages_upgradable() -> dict:
    """List upgradable packages on the Ubuntu host."""
    output = _ssh_exec("apt list --upgradable 2>/dev/null | tail -n+2")
    packages = []
    for line in output.split("\n"):
        if line.strip():
            parts = line.split("/")
            packages.append(parts[0] if parts else line.strip())
    return {"count": len(packages), "packages": packages[:50]}


@mcp.tool()
def host_journal(unit: str, lines: int = 200) -> str:
    """Read systemd journal for a unit (e.g., 'microk8s.daemon-kubelite', 'restic-backup')."""
    _validate_k8s_name(unit, "unit")  # reuse — same character class
    return _ssh_exec(f"journalctl -u {unit} --no-pager -n {int(lines)}")


@mcp.tool()
def host_failed_units() -> list[str]:
    """List failed systemd units on the host."""
    raw = _ssh_exec("systemctl --failed --no-legend --no-pager")
    return [line.strip() for line in raw.split("\n") if line.strip()]


@mcp.tool()
def host_security_audit() -> dict:
    """Quick security posture check: UFW, unattended-upgrades, last login, last reboot."""
    ufw = _ssh_exec("sudo ufw status 2>/dev/null || echo 'ufw not installed'")
    ua = _ssh_exec("dpkg -l unattended-upgrades 2>/dev/null | grep -q ^ii && echo 'installed' || echo 'not installed'")
    last_login = _ssh_exec("last -n 5 --time-format iso 2>/dev/null || last -n 5")
    last_reboot = _ssh_exec("who -b 2>/dev/null || uptime -s")
    return {
        "ufw": ufw,
        "unattended_upgrades": ua,
        "last_logins": last_login,
        "last_reboot": last_reboot,
    }


@mcp.tool()
def host_smart() -> dict:
    """SMART disk health summary for each disk on the host."""
    try:
        raw = _ssh_exec("sudo smartctl --scan 2>/dev/null | awk '{print $1}'")
        disks = {}
        for disk in raw.split("\n"):
            disk = disk.strip()
            if disk:
                info = _ssh_exec(f"sudo smartctl -H {disk} 2>/dev/null | grep -i 'overall\\|result'")
                disks[disk] = info.strip()
        return {"disks": disks}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def host_nfs_status() -> dict:
    """Check NFS mount status and reachability."""
    mounts = _ssh_exec("mount | grep nfs || echo 'no NFS mounts'")
    return {"nfs_mounts": mounts}


# ============================================================
# MEDIA AGGREGATE TOOLS
# ============================================================


@mcp.tool()
def media_pipeline_health() -> dict:
    """One-call health check of the entire media pipeline: Sonarr, Radarr, Prowlarr, qBittorrent, Plex."""
    results = {}
    for name, fn in [("sonarr", sonarr_health), ("radarr", radarr_health),
                     ("prowlarr", prowlarr_health), ("qbittorrent", qbt_status),
                     ("plex", plex_status)]:
        try:
            data = fn()
            results[name] = {"ok": True, "data": data}
        except Exception as e:
            results[name] = {"ok": False, "error": str(e)}
    all_ok = all(r.get("ok") for r in results.values())
    return {"all_ok": all_ok, "services": results}


@mcp.tool()
def media_disk_pressure() -> dict:
    """Cross-check disk usage from host, Sonarr, and Radarr disk space APIs."""
    result = {}
    try:
        result["host_disk"] = host_disk()
    except Exception as e:
        result["host_disk"] = {"error": str(e)}
    try:
        result["sonarr_disk"] = _sonarr().get("/diskspace")
    except Exception as e:
        result["sonarr_disk"] = {"error": str(e)}
    try:
        result["radarr_disk"] = _radarr().get("/diskspace")
    except Exception as e:
        result["radarr_disk"] = {"error": str(e)}
    return result


@mcp.tool()
def media_indexer_health() -> dict:
    """Test all Prowlarr indexers and report health."""
    try:
        return prowlarr_test_indexers()
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================
# GITOPS INTROSPECTION TOOLS (read-only repo analysis)
# ============================================================


@mcp.tool()
def gitops_drift() -> dict:
    """Compare cluster state vs last applied Flux revision for each kustomization."""
    try:
        raw = _ssh_exec("flux get kustomizations -A 2>&1")
        return {"drift_report": raw}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gitops_app_inventory() -> list[dict]:
    """Walk apps/**/kustomization.yaml and return registered apps with image + namespace."""
    try:
        raw = _ssh_exec(
            "cd ~/src/homelab && "
            "find apps -name kustomization.yaml -exec grep -l 'resources' {} \\; | sort"
        )
        apps = []
        for path in raw.split("\n"):
            path = path.strip()
            if not path:
                continue
            parts = path.split("/")
            if len(parts) >= 3:
                apps.append({
                    "category": parts[1],
                    "name": parts[2],
                    "path": path,
                })
        return apps
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
def gitops_secret_audit() -> list[dict]:
    """Find any Secret manifests with literal data blocks (potential plaintext leak)."""
    try:
        raw = _ssh_exec(
            "cd ~/src/homelab && "
            "grep -rl 'kind: Secret' apps/ clusters/ 2>/dev/null | "
            "xargs -I{} grep -l '^data:' {} 2>/dev/null || echo ''"
        )
        files = [f.strip() for f in raw.split("\n") if f.strip()]
        return [{"file": f, "warning": "Contains literal data: block — verify it's SOPS-encrypted"} for f in files]
    except Exception as e:
        return [{"error": str(e)}]


# ============================================================
# KUBERNETES TOOLS (via SSH + kubectl on the host)
# ============================================================

import json as _json
import re as _re
from datetime import datetime as _dt, timedelta as _td, timezone as _tz

# K8s resource name: RFC 1123 label (lowercase alnum + hyphens, max 253 chars)
_K8S_NAME_RE = _re.compile(r'^[a-z0-9]([a-z0-9.\-]*[a-z0-9])?$')
# Container image ref: alphanum + slashes, colons, dots, hyphens, underscores, @sha256:
_IMAGE_RE = _re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_./:@\-]*$')
# Duration: digits + h/m/s/d units
_DURATION_RE = _re.compile(r'^(\d+[dhms])+$')


def _validate_k8s_name(value: str, param: str) -> str:
    """Validate a Kubernetes resource name. Raises ValueError on invalid input."""
    if not value or len(value) > 253 or not _K8S_NAME_RE.match(value):
        raise ValueError(f"Invalid {param}: {value!r} — must be a valid K8s name (lowercase alnum + hyphens)")
    return value


def _validate_image(value: str) -> str:
    """Validate a container image reference. Raises ValueError on invalid input."""
    if not value or len(value) > 500 or not _IMAGE_RE.match(value):
        raise ValueError(f"Invalid image ref: {value!r}")
    return value


def _validate_duration(value: str) -> str:
    """Validate a duration string like '30m', '1h', '2h30m'. Raises ValueError on invalid input."""
    if not value:
        return value
    if not _DURATION_RE.match(value):
        raise ValueError(f"Invalid duration: {value!r} — use digits + h/m/s/d (e.g., '30m', '1h', '2d')")
    return value


def _kube(cmd: str, timeout: int = 60) -> str:
    """Run a kubectl command on the host via SSH."""
    return _ssh_exec(f"kubectl {cmd}", timeout=timeout)


@mcp.tool()
def kube_pods(ns: str = "", failing_only: bool = False) -> list[dict]:
    """List Kubernetes pods. Optionally filter to a namespace or only non-Running/Completed pods."""
    if ns:
        _validate_k8s_name(ns, "namespace")
    ns_flag = f"-n {ns}" if ns else "--all-namespaces"
    raw = _kube(f"get pods {ns_flag} -o json")
    data = _json.loads(raw)
    pods = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        status = item.get("status", {})
        phase = status.get("phase", "Unknown")
        restart_count = sum(
            cs.get("restartCount", 0)
            for cs in status.get("containerStatuses", [])
        )
        pod = {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "phase": phase,
            "restarts": restart_count,
            "node": item.get("spec", {}).get("nodeName"),
        }
        if failing_only and phase in ("Running", "Succeeded"):
            continue
        pods.append(pod)
    return pods


@mcp.tool()
def kube_describe(name: str, ns: str = "default") -> str:
    """Describe a Kubernetes pod (events, conditions, volume status)."""
    _validate_k8s_name(name, "pod name")
    _validate_k8s_name(ns, "namespace")
    return _kube(f"describe pod {name} -n {ns}")


@mcp.tool()
def kube_logs(name: str, ns: str = "default", container: str = "",
              tail: int = 200, since: str = "") -> str:
    """Get logs from a Kubernetes pod."""
    _validate_k8s_name(name, "pod name")
    _validate_k8s_name(ns, "namespace")
    if container:
        _validate_k8s_name(container, "container")
    if since:
        _validate_duration(since)
    cmd = f"logs {name} -n {ns} --tail={int(tail)}"
    if container:
        cmd += f" -c {container}"
    if since:
        cmd += f" --since={since}"
    return _kube(cmd)


@mcp.tool()
def kube_events(ns: str = "", kind: str = "", name: str = "",
                last: str = "30m") -> list[dict]:
    """Get recent Kubernetes events, sorted by time."""
    if ns:
        _validate_k8s_name(ns, "namespace")
    if name:
        _validate_k8s_name(name, "name")
    if last:
        _validate_duration(last)
    ns_flag = f"-n {ns}" if ns else "--all-namespaces"
    raw = _kube(f"get events {ns_flag} --sort-by='.lastTimestamp' -o json")
    data = _json.loads(raw)

    # Parse 'last' duration (e.g., "30m", "1h", "2h30m", "1d", "30s")
    cutoff = None
    if last:
        seconds = 0
        for match in _re.finditer(r'(\d+)([dhms])', last):
            val, unit = int(match.group(1)), match.group(2)
            if unit == 'd':
                seconds += val * 86400
            elif unit == 'h':
                seconds += val * 3600
            elif unit == 'm':
                seconds += val * 60
            else:
                seconds += val
        if seconds > 0:
            cutoff = _dt.now(_tz.utc) - _td(seconds=seconds)

    events = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        involved = item.get("involvedObject", {})
        ts = item.get("lastTimestamp") or meta.get("creationTimestamp", "")

        if kind and involved.get("kind", "").lower() != kind.lower():
            continue
        if name and involved.get("name", "") != name:
            continue

        event = {
            "time": ts,
            "type": item.get("type"),
            "reason": item.get("reason"),
            "object": f"{involved.get('kind', '')}/{involved.get('name', '')}",
            "message": item.get("message", ""),
            "namespace": meta.get("namespace"),
        }
        events.append(event)

    # Time filter
    if cutoff:
        filtered = []
        for e in events:
            try:
                t = _dt.fromisoformat(e["time"].replace("Z", "+00:00"))
                if t >= cutoff:
                    filtered.append(e)
            except (ValueError, TypeError):
                filtered.append(e)
        events = filtered

    return events[-100:]


@mcp.tool()
def kube_restart(deployment: str, ns: str = "default") -> str:
    """Rollout restart a Kubernetes deployment. This is the only mutating kube tool."""
    _check_readonly("kube_restart")
    _validate_k8s_name(deployment, "deployment")
    _validate_k8s_name(ns, "namespace")
    _audit("kube_restart", {"deployment": deployment, "ns": ns})
    return _kube(f"rollout restart deployment/{deployment} -n {ns}")


@mcp.tool()
def kube_rollout_status(deployment: str, ns: str = "default",
                        timeout: int = 60) -> str:
    """Check rollout status of a Kubernetes deployment."""
    _validate_k8s_name(deployment, "deployment")
    _validate_k8s_name(ns, "namespace")
    return _kube(f"rollout status deployment/{deployment} -n {ns} --timeout={int(timeout)}s",
                 timeout=int(timeout) + 10)


@mcp.tool()
def kube_image_present(image: str) -> dict:
    """Check if a container image is present in the containerd cache on the host."""
    _validate_image(image)
    try:
        raw = _ssh_exec("sudo crictl images --output json 2>/dev/null || echo '[]'")
        data = _json.loads(raw) if raw.startswith('{') or raw.startswith('[') else {}
        # Parse crictl JSON: images[].repoTags[] contains full image refs
        repo_tags = []
        for img in data.get("images", []):
            repo_tags.extend(img.get("repoTags", []))
        # Exact match or match with implicit :latest
        present = image in repo_tags or f"{image}:latest" in repo_tags
        return {"present": present, "image": image}
    except Exception as e:
        return {"present": False, "image": image, "error": str(e)}


@mcp.tool()
def kube_image_can_pull(image: str, timeout: int = 60) -> dict:
    """Test if the cluster can pull a container image by running a throwaway pod."""
    _audit("kube_image_can_pull", {"image": image, "timeout": timeout})
    _check_readonly("kube_image_can_pull")
    _validate_image(image)
    import uuid
    pod_name = f"mcp-pull-test-{uuid.uuid4().hex[:8]}"
    try:
        _kube(f"run {pod_name} --image={image} --restart=Never --command -- /bin/true")
        _kube(
            f"wait pod/{pod_name} -n default --for=condition=Ready --timeout={int(timeout)}s",
            timeout=int(timeout) + 10,
        )
        return {"can_pull": True, "image": image}
    except RuntimeError as e:
        return {"can_pull": False, "image": image, "error": str(e)}
    finally:
        try:
            _kube(f"delete pod {pod_name} -n default --ignore-not-found=true")
        except Exception:
            pass


# ============================================================
# FLUX TOOLS (via SSH + flux CLI on the host)
# ============================================================


def _flux(cmd: str, timeout: int = 60) -> str:
    """Run a flux command on the host via SSH."""
    return _ssh_exec(f"flux {cmd}", timeout=timeout)


@mcp.tool()
def flux_status() -> dict:
    """Get status of all Flux sources and kustomizations."""
    sources_raw = _ssh_exec("flux get sources all -A -o json 2>/dev/null || flux get sources git -A 2>&1")
    kust_raw = _ssh_exec("flux get kustomizations -A -o json 2>/dev/null || flux get kustomizations -A 2>&1")
    # Try JSON parse, fallback to raw text
    try:
        sources = _json.loads(sources_raw)
    except (ValueError, TypeError):
        sources = sources_raw
    try:
        kustomizations = _json.loads(kust_raw)
    except (ValueError, TypeError):
        kustomizations = kust_raw
    return {"sources": sources, "kustomizations": kustomizations}


@mcp.tool()
def flux_reconcile(target: str = "all") -> str:
    """Trigger Flux reconciliation. Target: 'all' (source + kustomization), 'source', or a kustomization name."""
    _check_readonly("flux_reconcile")
    _audit("flux_reconcile", {"target": target})
    if target == "all":
        src = _flux("reconcile source git flux-system")
        kust = _flux("reconcile kustomization flux-system")
        return f"Source: {src}\nKustomization: {kust}"
    elif target == "source":
        return _flux("reconcile source git flux-system")
    else:
        _validate_k8s_name(target, "kustomization")
        return _flux(f"reconcile kustomization {target}")


@mcp.tool()
def flux_suspend(kustomization: str = "flux-system") -> str:
    """Suspend a Flux kustomization (pauses reconciliation for hot-fix workflows)."""
    _check_readonly("flux_suspend")
    _validate_k8s_name(kustomization, "kustomization")
    _audit("flux_suspend", {"kustomization": kustomization})
    return _flux(f"suspend kustomization {kustomization}")


@mcp.tool()
def flux_resume(kustomization: str = "flux-system") -> str:
    """Resume a suspended Flux kustomization."""
    _check_readonly("flux_resume")
    _validate_k8s_name(kustomization, "kustomization")
    _audit("flux_resume", {"kustomization": kustomization})
    return _flux(f"resume kustomization {kustomization}")


@mcp.tool()
def flux_diff(kustomization: str = "flux-system") -> str:
    """Show what would change on next Flux reconciliation (dry-run diff)."""
    _validate_k8s_name(kustomization, "kustomization")
    try:
        return _flux(f"diff kustomization {kustomization}", timeout=120)
    except RuntimeError as e:
        return f"Diff failed (may need cluster access): {e}"


# ============================================================
# IMAGE TOOLS (registry queries — no cluster access needed)
# ============================================================

# Registry image ref validation reuses _IMAGE_RE from kube tools


@mcp.tool()
def image_list_tags(repo: str, registry: str = "docker.io",
                    limit: int = 50) -> dict:
    """List available tags for a container image from a registry (Docker Hub, GHCR, lscr.io)."""
    _validate_image(repo)
    try:
        import httpx
        if registry == "docker.io":
            # Docker Hub v2 API
            # Normalize: "nginx" -> "library/nginx"
            if "/" not in repo:
                repo = f"library/{repo}"
            token_resp = httpx.get(
                f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull",
                timeout=10,
            )
            token = token_resp.json().get("token", "")
            resp = httpx.get(
                f"https://registry-1.docker.io/v2/{repo}/tags/list",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            tags = resp.json().get("tags", [])
        elif registry in ("ghcr.io", "lscr.io"):
            resp = httpx.get(
                f"https://{registry}/v2/{repo}/tags/list",
                timeout=10,
            )
            tags = resp.json().get("tags", [])
        else:
            resp = httpx.get(
                f"https://{registry}/v2/{repo}/tags/list",
                timeout=10,
            )
            tags = resp.json().get("tags", [])
        return {"repo": repo, "registry": registry, "count": len(tags),
                "tags": tags[-limit:]}
    except Exception as e:
        return {"repo": repo, "registry": registry, "error": str(e)}


@mcp.tool()
def image_inspect(image: str) -> dict:
    """Inspect a container image manifest (digest, platforms, size)."""
    _validate_image(image)
    try:
        raw = _ssh_exec(f"skopeo inspect docker://{image} 2>&1 || "
                        f"crictl inspecti {image} 2>&1 || echo 'inspect unavailable'",
                        timeout=30)
        try:
            return _json.loads(raw)
        except (ValueError, TypeError):
            return {"raw": raw, "image": image}
    except Exception as e:
        return {"image": image, "error": str(e)}


@mcp.tool()
def image_compare_tags(repo: str, tag_a: str, tag_b: str,
                       registry: str = "docker.io") -> dict:
    """Compare two image tags by digest to check if they're the same image."""
    _validate_image(repo)
    try:
        import httpx
        if "/" not in repo and registry == "docker.io":
            repo = f"library/{repo}"

        if registry == "docker.io":
            token_resp = httpx.get(
                f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull",
                timeout=10,
            )
            token = token_resp.json().get("token", "")
            headers = {"Authorization": f"Bearer {token}",
                       "Accept": "application/vnd.docker.distribution.manifest.v2+json"}
            base = f"https://registry-1.docker.io/v2/{repo}/manifests"
        else:
            headers = {"Accept": "application/vnd.docker.distribution.manifest.v2+json"}
            base = f"https://{registry}/v2/{repo}/manifests"

        resp_a = httpx.head(f"{base}/{tag_a}", headers=headers, timeout=10)
        resp_b = httpx.head(f"{base}/{tag_b}", headers=headers, timeout=10)
        digest_a = resp_a.headers.get("docker-content-digest", "unknown")
        digest_b = resp_b.headers.get("docker-content-digest", "unknown")
        return {
            "repo": repo,
            "tag_a": tag_a, "digest_a": digest_a,
            "tag_b": tag_b, "digest_b": digest_b,
            "same_image": digest_a == digest_b and digest_a != "unknown",
        }
    except Exception as e:
        return {"repo": repo, "error": str(e)}


# ============================================================
# IKEA DIRIGERA TOOLS (smart home hub)
# ============================================================


def _dirigera():
    """Lazy-init Dirigera hub client. Returns None if not configured."""
    if "dirigera" in _clients:
        return _clients["dirigera"]
    cfg = get_dirigera_config()
    if not cfg.url or not cfg.api_key:
        return None
    try:
        import dirigera as _dlib
        # The lib uses HTTPS to https://<ip>:8443 by default; we just pass the IP.
        hub = _dlib.Hub(token=cfg.api_key, ip_address=cfg.url)
        _clients["dirigera"] = hub
        return hub
    except Exception as e:
        _clients["dirigera"] = None
        raise RuntimeError(f"Dirigera init failed: {e}") from e


def _need_dirigera():
    h = _dirigera()
    if h is None:
        raise RuntimeError(
            "Dirigera not configured. Set DIRIGERA_IP and DIRIGERA_TOKEN. "
            "Pair once with: generate-token <hub-ip>"
        )
    return h


def _dev_summary(d) -> dict:
    """Compact summary of any dirigera device, defensive against missing attrs."""
    a = getattr(d, "attributes", None)
    return {
        "id": getattr(d, "id", None),
        "name": getattr(a, "custom_name", None) if a else None,
        "type": getattr(d, "device_type", None) or getattr(d, "type", None),
        "model": getattr(a, "model", None) if a else None,
        "room": getattr(getattr(d, "room", None), "name", None),
        "reachable": getattr(d, "is_reachable", None),
    }


@mcp.tool()
def dirigera_status() -> dict:
    """Check Dirigera hub reachability, device counts, and warnings (low battery, unreachable)."""
    h = _need_dirigera()
    counts = {}
    warnings = []
    for kind, fn in [
        ("lights", "get_lights"),
        ("outlets", "get_outlets"),
        ("blinds", "get_blinds"),
        ("controllers", "get_controllers"),
        ("environment_sensors", "get_environment_sensors"),
        ("motion_sensors", "get_motion_sensors"),
        ("open_close_sensors", "get_open_close_sensors"),
        ("air_purifiers", "get_air_purifiers"),
        ("scenes", "get_scenes"),
    ]:
        try:
            devices = getattr(h, fn)()
            counts[kind] = len(devices)
            # Check for unreachable devices and low battery
            if kind != "scenes":
                for d in devices:
                    name = getattr(getattr(d, "attributes", None), "custom_name", None) or getattr(d, "id", "?")
                    if getattr(d, "is_reachable", None) is False:
                        warnings.append({"device": name, "type": kind, "issue": "unreachable"})
                    batt = getattr(getattr(d, "attributes", None), "battery_percentage", None)
                    if batt is not None and batt < 20:
                        warnings.append({"device": name, "type": kind, "issue": f"low_battery ({batt}%)"})
        except Exception as e:
            counts[kind] = f"error: {e}"
    return {"reachable": True, "counts": counts, "warnings": warnings}


@mcp.tool()
def dirigera_devices(kind: str = "all") -> dict:
    """List Dirigera devices, optionally filtered by kind.
    kind ∈ {all, lights, outlets, blinds, controllers, environment_sensors,
            motion_sensors, open_close_sensors, air_purifiers}."""
    h = _need_dirigera()
    kinds = ["lights", "outlets", "blinds", "controllers", "environment_sensors",
             "motion_sensors", "open_close_sensors", "air_purifiers"] \
            if kind == "all" else [kind]
    out: dict = {}
    for k in kinds:
        fn = "get_" + k
        if not hasattr(h, fn):
            out[k] = {"error": f"unknown kind {k}"}
            continue
        try:
            out[k] = [_dev_summary(d) for d in getattr(h, fn)()]
        except Exception as e:
            out[k] = {"error": str(e)}
    return out


@mcp.tool()
def dirigera_lights() -> list[dict]:
    """List all lights with their on/off state, brightness, and color temp."""
    h = _need_dirigera()
    out = []
    for l in h.get_lights():
        a = l.attributes
        out.append({
            **_dev_summary(l),
            "is_on": getattr(a, "is_on", None),
            "light_level": getattr(a, "light_level", None),
            "color_temperature": getattr(a, "color_temperature", None),
            "color_hue": getattr(a, "color_hue", None),
            "color_saturation": getattr(a, "color_saturation", None),
        })
    return out


def _find_light(h, name_or_id: str):
    for l in h.get_lights():
        if l.id == name_or_id or l.attributes.custom_name == name_or_id:
            return l
    raise RuntimeError(f"Light not found: {name_or_id}")


@mcp.tool()
def dirigera_set_light(name_or_id: str, on: bool | None = None,
                       brightness: int | None = None,
                       color_temperature: int | None = None,
                       hue: int | None = None,
                       saturation: float | None = None) -> dict:
    """Control a light by name or id.
    on: True/False to switch on/off.
    brightness: 1-100.
    color_temperature: kelvin (e.g. 2700-4000 typical range).
    hue (0-360) + saturation (0.0-1.0) for color bulbs."""
    _audit("dirigera_set_light", {"name_or_id": name_or_id, "on": on,
           "brightness": brightness, "color_temperature": color_temperature,
           "hue": hue, "saturation": saturation})
    _check_readonly("dirigera_set_light")
    h = _need_dirigera()
    light = _find_light(h, name_or_id)
    actions: list[str] = []
    if on is not None:
        light.set_light(lamp_on=bool(on)); actions.append(f"on={on}")
    if brightness is not None:
        light.set_light_level(light_level=int(brightness)); actions.append(f"brightness={brightness}")
    if color_temperature is not None:
        light.set_color_temperature(color_temp=int(color_temperature))
        actions.append(f"color_temp={color_temperature}")
    if hue is not None and saturation is not None:
        light.set_light_color(hue=int(hue), saturation=float(saturation))
        actions.append(f"color={hue}/{saturation}")
    return {"id": light.id, "name": light.attributes.custom_name, "applied": actions}


@mcp.tool()
def dirigera_outlets() -> list[dict]:
    """List smart outlets/plugs with on/off + power consumption when available."""
    h = _need_dirigera()
    out = []
    for o in h.get_outlets():
        a = o.attributes
        out.append({
            **_dev_summary(o),
            "is_on": getattr(a, "is_on", None),
            "current_active_power_w": getattr(a, "current_active_power", None),
            "total_energy_kwh": getattr(a, "total_energy_consumed", None),
        })
    return out


@mcp.tool()
def dirigera_set_outlet(name_or_id: str, on: bool) -> dict:
    """Turn a smart outlet on or off (by name or id)."""
    _audit("dirigera_set_outlet", {"name_or_id": name_or_id, "on": on})
    _check_readonly("dirigera_set_outlet")
    h = _need_dirigera()
    target = next((o for o in h.get_outlets()
                   if o.id == name_or_id or o.attributes.custom_name == name_or_id), None)
    if not target:
        raise RuntimeError(f"Outlet not found: {name_or_id}")
    target.set_on(outlet_on=bool(on))
    return {"id": target.id, "name": target.attributes.custom_name, "is_on": bool(on)}


@mcp.tool()
def dirigera_sensors() -> dict:
    """All sensor readings: environment (temp/humidity/PM2.5/VOC/CO2),
    motion (detected + battery), open/close (state + battery)."""
    h = _need_dirigera()
    env_list = []
    for s in h.get_environment_sensors():
        a = s.attributes
        env_list.append({
            **_dev_summary(s),
            "temperature_c": getattr(a, "current_temperature", None),
            "humidity_pct": getattr(a, "current_r_h", None),
            "pm25": getattr(a, "current_p_m25", None),
            "co2_ppm": getattr(a, "current_c_o2", None),
            "voc_index": getattr(a, "voc_index", None),
        })
    motion = []
    for s in h.get_motion_sensors():
        a = s.attributes
        motion.append({
            **_dev_summary(s),
            "is_detected": getattr(a, "is_detected", None),
            "battery_pct": getattr(a, "battery_percentage", None),
            "light_level": getattr(a, "light_level", None),
        })
    oc = []
    for s in h.get_open_close_sensors():
        a = s.attributes
        oc.append({
            **_dev_summary(s),
            "is_open": getattr(a, "is_open", None),
            "battery_pct": getattr(a, "battery_percentage", None),
        })
    return {"environment": env_list, "motion": motion, "open_close": oc}


@mcp.tool()
def dirigera_blinds() -> list[dict]:
    """List blinds with current/target level and battery."""
    h = _need_dirigera()
    out = []
    for b in h.get_blinds():
        a = b.attributes
        out.append({
            **_dev_summary(b),
            "current_level": getattr(a, "blinds_current_level", None),
            "target_level": getattr(a, "blinds_target_level", None),
            "state": getattr(a, "blinds_state", None),
            "battery_pct": getattr(a, "battery_percentage", None),
        })
    return out


@mcp.tool()
def dirigera_set_blind(name_or_id: str, target_level: int) -> dict:
    """Move a blind to a target level (0=fully closed, 100=fully open)."""
    _audit("dirigera_set_blind", {"name_or_id": name_or_id, "target_level": target_level})
    _check_readonly("dirigera_set_blind")
    h = _need_dirigera()
    target = next((b for b in h.get_blinds()
                   if b.id == name_or_id or b.attributes.custom_name == name_or_id), None)
    if not target:
        raise RuntimeError(f"Blind not found: {name_or_id}")
    target.set_target_level(target_level=int(target_level))
    return {"id": target.id, "name": target.attributes.custom_name,
            "target_level": int(target_level)}


@mcp.tool()
def dirigera_scenes() -> list[dict]:
    """List all scenes (id, name, last triggered)."""
    h = _need_dirigera()
    out = []
    for s in h.get_scenes():
        out.append({
            "id": s.id,
            "name": getattr(getattr(s, "info", None), "name", None),
            "type": str(getattr(s, "type", "")),
            "last_triggered": str(getattr(s, "last_triggered", "")) or None,
        })
    return out


@mcp.tool()
def dirigera_trigger_scene(name_or_id: str) -> dict:
    """Run a scene by name or id."""
    _audit("dirigera_trigger_scene", {"name_or_id": name_or_id})
    _check_readonly("dirigera_trigger_scene")
    h = _need_dirigera()
    target = next((s for s in h.get_scenes()
                   if s.id == name_or_id or
                   (getattr(s.info, "name", None) == name_or_id)), None)
    if not target:
        raise RuntimeError(f"Scene not found: {name_or_id}")
    target.trigger()
    return {"id": target.id, "name": target.info.name, "triggered": True}


# ============================================================
# APPLE TV / HOMEPOD TOOLS (pyatv)
# ============================================================
# Devices are configured via APPLE_TV_DEVICES env var, a JSON map:
#   {"Living Room": {"identifier":"AABB...", "credentials": {"companion":"...","airplay":"..."}}, ...}
# Pairing is one-time via the atvremote CLI installed by pyatv. The
# `apple_pair_*` workflow lives outside the agent (interactive PIN flow).

import asyncio as _aio
import json as _json


def _apple_devices_map() -> dict:
    raw = env("APPLE_TV_DEVICES", default="{}")
    try:
        return _json.loads(raw) if raw else {}
    except Exception as e:
        raise RuntimeError(f"APPLE_TV_DEVICES is not valid JSON: {e}")


def _apple_run(coro):
    """Run a pyatv coroutine to completion in a fresh event loop."""
    try:
        loop = _aio.new_event_loop()
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        except Exception:
            pass


async def _apple_connect(name: str):
    """Connect to a configured Apple device by friendly name."""
    import pyatv
    from pyatv.const import Protocol
    devices = _apple_devices_map()
    if name not in devices:
        raise RuntimeError(
            f"Apple device {name!r} not configured. Known: {list(devices)}. "
            "Run apple_scan, then pair via atvremote."
        )
    cfg = devices[name]
    identifier = cfg["identifier"]
    creds = cfg.get("credentials", {})

    loop = _aio.get_event_loop()
    found = await pyatv.scan(loop=loop, identifier=identifier, timeout=5)
    if not found:
        raise RuntimeError(f"Device {name!r} (id={identifier}) not found on LAN")
    conf = found[0]
    # Re-attach saved credentials per protocol
    name_to_proto = {
        "companion": Protocol.Companion,
        "airplay":   Protocol.AirPlay,
        "raop":      Protocol.RAOP,
        "mrp":       Protocol.MRP,
    }
    for proto_name, cred in creds.items():
        proto = name_to_proto.get(proto_name)
        if proto:
            conf.set_credentials(proto, cred)
    atv = await pyatv.connect(conf, loop=loop)
    return atv, conf


@mcp.tool()
def apple_scan(timeout: int = 5) -> list[dict]:
    """Scan the LAN for Apple TVs and HomePods. Returns identifiers + names
    + supported protocols. Use this BEFORE pairing to discover devices."""
    async def _scan():
        import pyatv
        loop = _aio.get_event_loop()
        results = await pyatv.scan(loop=loop, timeout=timeout)
        out = []
        for r in results:
            out.append({
                "name": r.name,
                "identifier": r.identifier,
                "address": str(r.address),
                "model": str(r.device_info.model) if r.device_info else None,
                "model_str": getattr(r.device_info, "raw_model", None) if r.device_info else None,
                "os": str(r.device_info.operating_system) if r.device_info else None,
                "protocols": sorted({str(s.protocol).split(".")[-1] for s in r.services}),
            })
        return out
    return _apple_run(_scan())


@mcp.tool()
def apple_devices() -> dict:
    """List configured Apple devices (from APPLE_TV_DEVICES env var)."""
    cfg = _apple_devices_map()
    return {
        name: {
            "identifier": v["identifier"],
            "paired_protocols": list(v.get("credentials", {}).keys()),
        }
        for name, v in cfg.items()
    }


@mcp.tool()
def apple_now_playing(device: str) -> dict:
    """What's playing on a configured Apple device."""
    async def _np():
        atv, _ = await _apple_connect(device)
        try:
            p = atv.metadata.playing if hasattr(atv.metadata, "playing") else await atv.metadata.playing()
            # pyatv 0.14+: metadata.playing is async
            if hasattr(p, "__await__"):
                p = await p
            return {
                "device": device,
                "title": getattr(p, "title", None),
                "artist": getattr(p, "artist", None),
                "album": getattr(p, "album", None),
                "app": getattr(getattr(atv, "apps", None), "app_list", lambda: None) and None,
                "device_state": str(getattr(p, "device_state", "")).split(".")[-1],
                "media_type": str(getattr(p, "media_type", "")).split(".")[-1],
                "position": getattr(p, "position", None),
                "total_time": getattr(p, "total_time", None),
            }
        finally:
            atv.close()
    return _apple_run(_np())


@mcp.tool()
def apple_play_pause(device: str) -> dict:
    """Toggle play/pause on a configured Apple device."""
    _audit("apple_play_pause", {"device": device})
    _check_readonly("apple_play_pause")
    async def _pp():
        atv, _ = await _apple_connect(device)
        try:
            await atv.remote_control.play_pause()
            return {"device": device, "ok": True}
        finally:
            atv.close()
    return _apple_run(_pp())


@mcp.tool()
def apple_volume(device: str, level: float | None = None) -> dict:
    """Get or set volume (0.0-100.0). Omit level to read current value."""
    _audit("apple_volume", {"device": device, "level": level})
    if level is not None:
        _check_readonly("apple_volume")
    async def _vol():
        atv, _ = await _apple_connect(device)
        try:
            if level is None:
                v = atv.audio.volume
                return {"device": device, "volume": v}
            await atv.audio.set_volume(float(level))
            return {"device": device, "volume": float(level)}
        finally:
            atv.close()
    return _apple_run(_vol())


@mcp.tool()
def apple_remote(device: str, key: str) -> dict:
    """Send a remote-control key. Valid keys: menu, home, select, up, down,
    left, right, play, pause, stop, next, previous, top_menu, suspend, wakeup."""
    _audit("apple_remote", {"device": device, "key": key})
    _check_readonly("apple_remote")
    async def _rc():
        atv, _ = await _apple_connect(device)
        try:
            fn = getattr(atv.remote_control, key, None)
            if fn is None:
                raise RuntimeError(f"Unknown remote key: {key}")
            await fn()
            return {"device": device, "key": key, "ok": True}
        finally:
            atv.close()
    return _apple_run(_rc())


@mcp.tool()
def apple_apps(device: str) -> list[dict]:
    """List apps installed on the Apple TV (Companion protocol required)."""
    async def _apps():
        atv, _ = await _apple_connect(device)
        try:
            apps = await atv.apps.app_list()
            return [{"name": a.name, "identifier": a.identifier} for a in apps]
        finally:
            atv.close()
    return _apple_run(_apps())


@mcp.tool()
def apple_launch_app(device: str, app: str) -> dict:
    """Launch an app on the Apple TV by identifier (e.g. com.netflix.Netflix)
    or by display name (case-insensitive partial match)."""
    _audit("apple_launch_app", {"device": device, "app": app})
    _check_readonly("apple_launch_app")
    async def _launch():
        atv, _ = await _apple_connect(device)
        try:
            target_id = app
            if "." not in app:
                apps = await atv.apps.app_list()
                m = [a for a in apps if app.lower() in a.name.lower()]
                if not m:
                    raise RuntimeError(f"No app matching {app!r}. Try apple_apps() to list.")
                target_id = m[0].identifier
                app_name = m[0].name
            else:
                app_name = app
            await atv.apps.launch_app(target_id)
            return {"device": device, "launched": app_name, "identifier": target_id}
        finally:
            atv.close()
    return _apple_run(_launch())


@mcp.tool()
def apple_run_shortcut(device: str, name: str) -> dict:
    """Run an Apple Shortcut by exact name on the target Apple TV.
    The Shortcut must already exist on iCloud and be enabled for Apple TV.
    This is the gateway to HomeKit scenes — wrap any Home scene in a Shortcut
    and trigger it from here."""
    _audit("apple_run_shortcut", {"device": device, "name": name})
    _check_readonly("apple_run_shortcut")
    async def _run():
        atv, _ = await _apple_connect(device)
        try:
            # pyatv's apps API exposes launch_app; Shortcuts are launched via a
            # special URL scheme: shortcuts://run-shortcut?name=<NAME>
            from urllib.parse import quote
            url = f"shortcuts://run-shortcut?name={quote(name)}"
            await atv.apps.launch_app(url)
            return {"device": device, "shortcut": name, "ok": True}
        finally:
            atv.close()
    return _apple_run(_run())


# ============================================================
# UBIQUITI UNIFI TOOLS (UDM / UDM-Pro / Cloud Key via aiounifi)
# ============================================================

def _unifi_run(coro):
    """Run a UniFi coroutine to completion in a fresh aiohttp session."""
    async def _wrap():
        import aiohttp
        cfg = get_unifi_config()
        if not cfg["host"]:
            raise RuntimeError("UniFi not configured. Set UNIFI_HOST/USER/PASS.")
        from aiounifi.controller import Controller
        from aiounifi.models.configuration import Configuration
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            controller = Controller(Configuration(
                session,
                cfg["host"],
                username=cfg["username"],
                password=cfg["password"],
                port=cfg["port"],
                site=cfg["site"],
                ssl_context=False,           # UDM uses self-signed cert
            ))
            # Retry login once with backoff on UDM auth rate-limit.
            from aiounifi.errors import AuthenticationRateLimitError, ResponseError
            for attempt in range(2):
                try:
                    await controller.login()
                    break
                except (AuthenticationRateLimitError, ResponseError) as e:
                    msg = str(e)
                    if "429" not in msg and "RATE" not in msg.upper() and "LIMIT" not in msg.upper():
                        raise
                    if attempt == 1:
                        raise RuntimeError(
                            "UniFi rate-limited login (429). Wait 5–15 min before retrying. "
                            f"Underlying: {type(e).__name__}: {msg[:200]}"
                        ) from e
                    await _aio.sleep(15)
            return await coro(controller)
    loop = _aio.new_event_loop()
    try:
        return loop.run_until_complete(_wrap())
    finally:
        loop.close()


def _client_summary(c) -> dict:
    """Compact JSON-safe view of a UniFi client."""
    return {
        "mac":        c.mac,
        "ip":         c.ip,
        "hostname":   c.hostname or c.name or "",
        "name":       c.name,
        "is_wired":   c.is_wired,
        "is_guest":   c.is_guest,
        "blocked":    c.blocked,
        "essid":      c.essid,
        "ap_mac":     c.access_point_mac,
        "switch_mac": getattr(c, "switch_mac", "") or "",
        "vlan":       getattr(c, "vlan", None),
        "uptime":     getattr(c, "uptime", 0),
        "last_seen":  c.last_seen,
        "rx_kbps":    getattr(c, "rx_rate", 0),
        "tx_kbps":    getattr(c, "tx_rate", 0),
    }


def _resolve_mac(controller, name_or_mac: str) -> str:
    """Accept a MAC, hostname, or display name; return the matching MAC."""
    if ":" in name_or_mac and len(name_or_mac) == 17:
        return name_or_mac.lower()
    needle = name_or_mac.lower()
    for c in controller.clients.values():
        for f in (c.hostname, c.name, c.device_name):
            if f and needle == f.lower():
                return c.mac
    # also check known clients (offline)
    for c in controller.clients_all.values():
        for f in (c.hostname, c.name):
            if f and needle == f.lower():
                return c.mac
    raise RuntimeError(f"No UniFi client found matching {name_or_mac!r}")


@mcp.tool()
def unifi_health() -> dict:
    """UniFi controller info: version, uptime, hostname, alarms, update available."""
    async def _h(controller):
        await controller.system_information.update()
        info = next(iter(controller.system_information.values()), None)
        if info is None:
            return {"ok": False, "error": "no system_information returned"}
        return {
            "ok": True,
            "name":             info.name,
            "hostname":         info.hostname,
            "version":          info.version,
            "previous_version": info.previous_version,
            "update_available": info.update_available,
            "device_type":      info.device_type,
            "uptime_seconds":   info.uptime,
            "external_ip":      info.ip_address,
        }
    return _unifi_run(_h)


@mcp.tool()
def unifi_clients(active_only: bool = True, top: int = 50) -> list[dict]:
    """List UniFi clients. active_only=True returns currently-connected only.
    Sorted by last_seen desc; truncated to `top`."""
    async def _c(controller):
        await controller.clients.update()
        if not active_only:
            await controller.clients_all.update()
            items = list(controller.clients_all.values())
        else:
            items = list(controller.clients.values())
        items.sort(key=lambda x: getattr(x, "last_seen", 0), reverse=True)
        return [_client_summary(c) for c in items[:top]]
    return _unifi_run(_c)


@mcp.tool()
def unifi_client(name_or_mac: str) -> dict:
    """Detail on a single client by MAC, hostname, or display name."""
    async def _c(controller):
        await controller.clients.update()
        await controller.clients_all.update()
        mac = _resolve_mac(controller, name_or_mac).lower()
        c = controller.clients.get(mac) or controller.clients_all.get(mac)
        if c is None:
            raise RuntimeError(f"client {mac} not found after lookup")
        return _client_summary(c)
    return _unifi_run(_c)


@mcp.tool()
def unifi_block(name_or_mac: str) -> dict:
    """Block a client from accessing the network (kid bedtime / device quarantine)."""
    _audit("unifi_block", {"name_or_mac": name_or_mac})
    _check_readonly("unifi_block")
    async def _b(controller):
        await controller.clients.update()
        await controller.clients_all.update()
        mac = _resolve_mac(controller, name_or_mac)
        await controller.clients.block(mac)
        return {"mac": mac, "blocked": True}
    return _unifi_run(_b)


@mcp.tool()
def unifi_unblock(name_or_mac: str) -> dict:
    """Lift a network block on a client."""
    _audit("unifi_unblock", {"name_or_mac": name_or_mac})
    _check_readonly("unifi_unblock")
    async def _u(controller):
        await controller.clients.update()
        await controller.clients_all.update()
        mac = _resolve_mac(controller, name_or_mac)
        await controller.clients.unblock(mac)
        return {"mac": mac, "blocked": False}
    return _unifi_run(_u)


@mcp.tool()
def unifi_reconnect(name_or_mac: str) -> dict:
    """Force a wireless client to reconnect (kick from current AP)."""
    _audit("unifi_reconnect", {"name_or_mac": name_or_mac})
    _check_readonly("unifi_reconnect")
    async def _r(controller):
        await controller.clients.update()
        mac = _resolve_mac(controller, name_or_mac)
        await controller.clients.reconnect(mac)
        return {"mac": mac, "reconnected": True}
    return _unifi_run(_r)


@mcp.tool()
def unifi_devices() -> list[dict]:
    """List UniFi network hardware (UAPs, switches, gateways)."""
    async def _d(controller):
        await controller.devices.update()
        out = []
        for d in controller.devices.values():
            out.append({
                "mac":        d.mac,
                "name":       d.raw.get("name", ""),
                "model":      d.raw.get("model", ""),
                "type":       d.raw.get("type", ""),
                "ip":         d.raw.get("ip", ""),
                "version":    d.raw.get("version", ""),
                "uptime":     d.raw.get("uptime", 0),
                "state":      d.raw.get("state", None),
                "num_clients": d.raw.get("num_sta", 0),
            })
        return out
    return _unifi_run(_d)


@mcp.tool()
def unifi_wlans() -> list[dict]:
    """List configured WLAN/SSIDs."""
    async def _w(controller):
        await controller.wlans.update()
        out = []
        for w in controller.wlans.values():
            out.append({
                "id":       w.id,
                "name":     w.name,
                "enabled":  w.enabled,
                "is_guest": getattr(w, "is_guest", False),
                "security": getattr(w, "security", ""),
                "vlan":     getattr(w, "vlan", None),
                "passphrase": getattr(w, "x_passphrase", "") or "",
            })
        return out
    return _unifi_run(_w)


@mcp.tool()
def unifi_wlan_set(name: str, enabled: bool) -> dict:
    """Enable or disable an SSID by name."""
    _audit("unifi_wlan_set", {"name": name, "enabled": enabled})
    _check_readonly("unifi_wlan_set")
    async def _s(controller):
        await controller.wlans.update()
        target = next((w for w in controller.wlans.values() if w.name == name), None)
        if not target:
            raise RuntimeError(f"WLAN {name!r} not found")
        if enabled:
            await controller.wlans.enable(target)
        else:
            await controller.wlans.disable(target)
        return {"name": target.name, "enabled": enabled}
    return _unifi_run(_s)


@mcp.tool()
def unifi_port_forwards() -> list[dict]:
    """List port forwarding rules."""
    async def _pf(controller):
        await controller.port_forwarding.update()
        out = []
        for r in controller.port_forwarding.values():
            out.append({
                "id":         r.id,
                "name":       r.raw.get("name", ""),
                "enabled":    r.raw.get("enabled", False),
                "src":        r.raw.get("src", ""),
                "dst_port":   r.raw.get("dst_port", ""),
                "fwd_ip":     r.raw.get("fwd", ""),
                "fwd_port":   r.raw.get("fwd_port", ""),
                "protocol":   r.raw.get("proto", ""),
            })
        return out
    return _unifi_run(_pf)


@mcp.tool()
def unifi_top_talkers(top: int = 10) -> list[dict]:
    """Top bandwidth users right now (by current rx+tx rate, kbps)."""
    async def _t(controller):
        await controller.clients.update()
        items = list(controller.clients.values())
        def total(c):
            return (getattr(c, "rx_rate", 0) or 0) + (getattr(c, "tx_rate", 0) or 0)
        items.sort(key=total, reverse=True)
        out = []
        for c in items[:top]:
            out.append({
                "name":   c.name or c.hostname or c.mac,
                "mac":    c.mac,
                "ip":     c.ip,
                "rx_kbps": getattr(c, "rx_rate", 0),
                "tx_kbps": getattr(c, "tx_rate", 0),
                "total_kbps": total(c),
                "essid":  c.essid,
            })
        return out
    return _unifi_run(_t)


# ============================================================
# CROSS-SERVICE TOOLS
# ============================================================


@mcp.tool()
def homelab_overview() -> dict:
    """Get a high-level overview of all homelab services in one call."""
    results = {}

    # Each service in a try/except so one failure doesn't kill the whole overview
    try:
        s = _sonarr()
        health = s.get("/health")
        results["sonarr"] = {"ok": len(health) == 0, "issues": len(health)}
    except Exception as e:
        results["sonarr"] = {"ok": False, "error": str(e)}

    try:
        r = _radarr()
        health = r.get("/health")
        results["radarr"] = {"ok": len(health) == 0, "issues": len(health)}
    except Exception as e:
        results["radarr"] = {"ok": False, "error": str(e)}

    try:
        p = _prowlarr()
        health = p.get("/health")
        results["prowlarr"] = {"ok": len(health) == 0, "issues": len(health)}
    except Exception as e:
        results["prowlarr"] = {"ok": False, "error": str(e)}

    try:
        q = _qbt()
        transfer = q.get("/transfer/info")
        results["qbittorrent"] = {
            "ok": True,
            "dl_speed": transfer.get("dl_info_speed", 0),
            "connection": transfer.get("connection_status"),
        }
    except Exception as e:
        results["qbittorrent"] = {"ok": False, "error": str(e)}

    try:
        px = _plex()
        identity = px.get("/")
        mc = identity.get("MediaContainer", {})
        sessions = px.get("/status/sessions")
        results["plex"] = {
            "ok": True,
            "version": mc.get("version"),
            "active_sessions": sessions.get("MediaContainer", {}).get("size", 0),
        }
    except Exception as e:
        results["plex"] = {"ok": False, "error": str(e)}

    try:
        hb = _homebridge()
        status = hb.get("/status/homebridge")
        results["homebridge"] = {"ok": status.get("status") == "up",
                                  "status": status.get("status")}
    except Exception as e:
        results["homebridge"] = {"ok": False, "error": str(e)}

    try:
        import httpx
        cfg = get_scrypted_config()
        resp = httpx.get(cfg.url, timeout=5, follow_redirects=True)
        results["scrypted"] = {"ok": resp.status_code in (200, 301, 302)}
    except Exception as e:
        results["scrypted"] = {"ok": False, "error": str(e)}

    try:
        h = _dirigera()
        if h is None:
            results["dirigera"] = {"ok": False, "error": "not configured"}
        else:
            lights = h.get_lights()
            results["dirigera"] = {
                "ok": True,
                "lights_total": len(lights),
                "lights_on": sum(1 for l in lights if getattr(l.attributes, "is_on", False)),
            }
    except Exception as e:
        results["dirigera"] = {"ok": False, "error": str(e)}

    # Kubernetes cluster summary
    try:
        pods = kube_pods()
        failing = [p for p in pods if p["phase"] not in ("Running", "Succeeded")]
        results["kubernetes"] = {
            "ok": len(failing) == 0,
            "total_pods": len(pods),
            "failing_pods": len(failing),
            "failing": [f"{p['namespace']}/{p['name']} ({p['phase']})" for p in failing[:5]],
        }
    except Exception as e:
        results["kubernetes"] = {"ok": False, "error": str(e)}

    # Resource pressure (node allocation)
    try:
        raw = _kube("describe node home 2>/dev/null | grep -A5 'Allocated resources'", timeout=15)
        results["resource_pressure"] = {"raw": raw.strip()}
    except Exception as e:
        results["resource_pressure"] = {"error": str(e)}

    return results


# ============================================================
# CERT-MANAGER TOOLS (read-only)
# ============================================================


@mcp.tool()
def cert_manager_status() -> dict:
    """Report cert-manager health: ClusterIssuers + Certificates with Ready/age/secret.

    Returns ``{"installed": False}`` if the CRDs are not present (e.g. before
    cert-manager has reconciled). Read-only — never mutates.
    """
    # Probe for CRDs first; missing CRDs is a normal state, not an error.
    try:
        _kube("get crd clusterissuers.cert-manager.io --no-headers", timeout=15)
    except RuntimeError as e:
        if "NotFound" in str(e) or "not found" in str(e).lower():
            return {"installed": False, "reason": "cert-manager CRDs not present"}
        raise

    issuers: list[dict] = []
    try:
        raw = _kube("get clusterissuers -o json", timeout=20)
        for item in _json.loads(raw).get("items", []):
            meta = item.get("metadata", {})
            conds = item.get("status", {}).get("conditions", [])
            ready = next((c for c in conds if c.get("type") == "Ready"), {})
            issuers.append({
                "name": meta.get("name"),
                "ready": ready.get("status") == "True",
                "reason": ready.get("reason"),
                "message": ready.get("message"),
            })
    except RuntimeError as e:
        issuers = [{"error": str(e)}]

    certs: list[dict] = []
    try:
        raw = _kube("get certificates -A -o json", timeout=20)
        for item in _json.loads(raw).get("items", []):
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            conds = status.get("conditions", [])
            ready = next((c for c in conds if c.get("type") == "Ready"), {})
            certs.append({
                "namespace": meta.get("namespace"),
                "name": meta.get("name"),
                "ready": ready.get("status") == "True",
                "reason": ready.get("reason"),
                "secret": spec.get("secretName"),
                "issuer": spec.get("issuerRef", {}).get("name"),
                "not_after": status.get("notAfter"),
                "renewal_time": status.get("renewalTime"),
            })
    except RuntimeError as e:
        certs = [{"error": str(e)}]

    return {
        "installed": True,
        "issuers": issuers,
        "certificates": certs,
        "issuer_count": len(issuers),
        "cert_count": len(certs),
        "not_ready_count": sum(1 for c in certs if c.get("ready") is False),
    }


# ============================================================
# INGRESS PROBE (read-only)
# ============================================================

# Allowed ingress hostnames: only ${HOMELAB_INGRESS_DOMAIN} subdomains, to
# prevent SSRF. The actual regex is built from env at call time so source
# carries no operator domain.


@mcp.tool()
def ingress_probe(host: str) -> dict:
    """HTTPS probe of a homelab ingress: status code, cert issuer/expiry, backend svc.

    The ``host`` parameter MUST match ``^[a-z0-9-]+\\.${HOMELAB_INGRESS_DOMAIN}$``
    where ``HOMELAB_INGRESS_DOMAIN`` is the operator's ingress apex —
    arbitrary hostnames are rejected to prevent SSRF (the probe runs from
    the homelab host, which has access to private networks).
    """
    host_re = ingress_host_re()
    if not host or not host_re.match(host):
        raise ValueError(
            f"Invalid host: {host!r} — must match {host_re.pattern}"
        )
    # Resolve to the operator's ingress IP so we test the cluster path,
    # not whatever public DNS resolves to.
    ingress_ip = homelab_ingress_ip()
    cmd = (
        f"curl -sI --max-time 10 --resolve {host}:443:{ingress_ip} "
        f"https://{host} -o /dev/null -w 'HTTP:%{{http_code}}\\n' && "
        f"echo --CERT-- && "
        f"echo | openssl s_client -servername {host} -connect {ingress_ip}:443 "
        f"-verify_return_error 2>/dev/null </dev/null | "
        f"openssl x509 -noout -subject -issuer -enddate 2>/dev/null"
    )
    try:
        out = _ssh_exec(cmd, timeout=20)
    except RuntimeError as e:
        return {"host": host, "reachable": False, "error": str(e)}

    result: dict = {"host": host, "reachable": True}
    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("HTTP:"):
            try:
                result["http_code"] = int(line.split(":", 1)[1])
            except ValueError:
                pass
        elif line.startswith("subject="):
            result["cert_subject"] = line.partition("=")[2]
        elif line.startswith("issuer="):
            result["cert_issuer"] = line.partition("=")[2]
        elif line.startswith("notAfter="):
            result["cert_not_after"] = line.partition("=")[2]
    result["reachable"] = result.get("http_code") is not None
    return result


# ============================================================
# CLOUDFLARE DNS TOOLS (read-only)
# ============================================================

# Allowed DNS zones: pinned to operator-supplied list (CF_ALLOWED_ZONES env).
# Empty list means cf_dns_* tools refuse all zones.
# DNS record name: lowercase RFC-1035 with optional dots
_DNS_NAME_RE = _re.compile(r'^[a-z0-9][a-z0-9.\-]*[a-z0-9]$')


def _cf_token() -> str:
    """Read the Cloudflare API token from env or fail loudly."""
    tok = env("CLOUDFLARE_API_TOKEN") or env("CF_DNS_API_TOKEN")
    if not tok:
        raise RuntimeError(
            "CLOUDFLARE_API_TOKEN not set. Either export it or pull from the "
            "in-cluster secret: "
            "kubectl get secret cloudflare-api-token -o jsonpath='{.data.CF_DNS_API_TOKEN}' | base64 -d"
        )
    return tok


def _cf_get(path: str, **params) -> dict:
    """GET against the Cloudflare v4 API."""
    import httpx
    r = httpx.get(
        f"https://api.cloudflare.com/client/v4{path}",
        headers={"Authorization": f"Bearer {_cf_token()}"},
        params=params,
        timeout=15.0,
    )
    r.raise_for_status()
    return r.json()


@mcp.tool()
def cf_dns_list(zone: str = "") -> dict:
    """List all DNS records in a Cloudflare zone (read-only).

    ``zone`` defaults to ``CF_DEFAULT_ZONE`` (or first entry of
    ``CF_ALLOWED_ZONES`` if unset). The resolved zone MUST be in
    ``CF_ALLOWED_ZONES``.
    """
    allowed = cf_allowed_zones()
    zone = (zone or cf_default_zone()).strip().lower()
    if zone not in allowed:
        raise ValueError(
            f"Zone {zone!r} not allowed. Allowed: {sorted(allowed)}"
        )
    zones = _cf_get("/zones", name=zone).get("result", [])
    if not zones:
        return {"zone": zone, "found": False, "records": []}
    zone_id = zones[0]["id"]
    records = _cf_get(f"/zones/{zone_id}/dns_records", per_page=200).get("result", [])
    return {
        "zone": zone,
        "zone_id": zone_id,
        "found": True,
        "count": len(records),
        "records": [
            {"name": r.get("name"), "type": r.get("type"),
             "content": r.get("content"), "ttl": r.get("ttl"),
             "proxied": r.get("proxied"), "comment": r.get("comment")}
            for r in records
        ],
    }


@mcp.tool()
def cf_dns_get(name: str, zone: str = "") -> dict:
    """Look up a single DNS record by name in a Cloudflare zone (read-only)."""
    allowed = cf_allowed_zones()
    zone = (zone or cf_default_zone()).strip().lower()
    if zone not in allowed:
        raise ValueError(
            f"Zone {zone!r} not allowed. Allowed: {sorted(allowed)}"
        )
    if not name or len(name) > 253 or not _DNS_NAME_RE.match(name):
        raise ValueError(f"Invalid DNS name: {name!r}")
    zones = _cf_get("/zones", name=zone).get("result", [])
    if not zones:
        return {"name": name, "found": False}
    zone_id = zones[0]["id"]
    records = _cf_get(f"/zones/{zone_id}/dns_records", name=name).get("result", [])
    return {
        "name": name,
        "zone": zone,
        "found": bool(records),
        "records": [
            {"id": r.get("id"), "type": r.get("type"), "content": r.get("content"),
             "ttl": r.get("ttl"), "proxied": r.get("proxied"),
             "comment": r.get("comment")}
            for r in records
        ],
    }


# ============================================================
# PROWLARR INDEXER WRITE TOOLS
# ============================================================

# Indexer "definitionName" is Prowlarr's internal ID. Allow common torznab
# generic + a small allow-list of named indexers that we actually use.
_PROWLARR_DEF_RE = _re.compile(r'^[a-zA-Z0-9_\-]{2,64}$')


@mcp.tool()
def prowlarr_add_torznab_indexer(
    name: str,
    url: str,
    api_key: str,
    categories: list[int] | None = None,
    definition: str = "Generic Torznab",
) -> dict:
    """Add a Torznab indexer to Prowlarr.

    Scoped to Torznab only (covers ~90% of self-host indexers including most
    private trackers). For other indexer kinds, use the Prowlarr UI directly —
    a generic ``add_indexer`` tool would be ``run_arbitrary_request`` in disguise.

    Args:
        name: Display name (1–64 chars, alnum + space/hyphen/underscore).
        url: Indexer base URL (must be http(s)://).
        api_key: Indexer API key (passed to Prowlarr, not logged in audit).
        categories: Newznab category IDs (defaults to ``[5000]`` = TV).
        definition: Prowlarr definition name; defaults to ``Generic Torznab``.
    """
    _audit("prowlarr_add_torznab_indexer", {"name": name, "url": url})
    _check_readonly("prowlarr_add_torznab_indexer")

    if not name or len(name) > 64 or not _re.match(r'^[a-zA-Z0-9 _\-]+$', name):
        raise ValueError(f"Invalid indexer name: {name!r}")
    if not url or not _re.match(r'^https?://[a-zA-Z0-9._\-:/]+$', url) or len(url) > 500:
        raise ValueError(f"Invalid url: {url!r}")
    if not api_key or len(api_key) > 200 or not _re.match(r'^[a-zA-Z0-9]+$', api_key):
        raise ValueError("Invalid api_key (must be alnum, 1-200 chars)")
    if not _PROWLARR_DEF_RE.match(definition):
        raise ValueError(f"Invalid definition: {definition!r}")
    cats = categories if categories is not None else [5000]
    if not isinstance(cats, list) or not all(isinstance(c, int) and 0 < c < 10_000 for c in cats):
        raise ValueError("categories must be a list of ints in (0, 10000)")

    payload = {
        "name": name,
        "implementation": "Torznab",
        "implementationName": "Torznab",
        "configContract": "TorznabSettings",
        "definitionName": definition,
        "protocol": "torrent",
        "enable": True,
        "priority": 25,
        "fields": [
            {"name": "baseUrl", "value": url},
            {"name": "apiKey", "value": api_key},
            {"name": "categories", "value": cats},
        ],
    }
    c = _prowlarr()
    result = c.post("/indexer", json=payload)
    return {
        "id": result.get("id"),
        "name": result.get("name"),
        "implementation": result.get("implementation"),
        "enable": result.get("enable"),
    }


@mcp.tool()
def prowlarr_remove_indexer(indexer_id: int) -> dict:
    """Remove a Prowlarr indexer by numeric ID."""
    _audit("prowlarr_remove_indexer", {"indexer_id": indexer_id})
    _check_readonly("prowlarr_remove_indexer")
    if not isinstance(indexer_id, int) or indexer_id < 1 or indexer_id > 10_000:
        raise ValueError(f"Invalid indexer_id: {indexer_id!r}")
    c = _prowlarr()
    # Servarr DELETE returns 200/204 with empty body; reuse the underlying client.
    r = c._client.delete(f"{c.base}/api/{c.api_version}/indexer/{indexer_id}")
    r.raise_for_status()
    return {"id": indexer_id, "deleted": True}


# ============================================================
# KUBE PVC USAGE (read-only, capacity from API only — no exec)
# ============================================================


@mcp.tool()
def kube_pvc_usage(ns: str = "") -> list[dict]:
    """List PersistentVolumeClaims with capacity and bind status.

    Returns capacity from the PVC spec (NOT live ``df`` usage) — that would
    require ``kubectl exec`` which is an arbitrary-execution surface. For real
    disk usage, use ``host_disk`` or ``netdata`` metrics.
    """
    if ns:
        _validate_k8s_name(ns, "namespace")
    ns_flag = f"-n {ns}" if ns else "--all-namespaces"
    raw = _kube(f"get pvc {ns_flag} -o json", timeout=30)
    out = []
    for item in _json.loads(raw).get("items", []):
        meta = item.get("metadata", {})
        spec = item.get("spec", {})
        status = item.get("status", {})
        out.append({
            "namespace": meta.get("namespace"),
            "name": meta.get("name"),
            "phase": status.get("phase"),
            "capacity": status.get("capacity", {}).get("storage"),
            "requested": spec.get("resources", {}).get("requests", {}).get("storage"),
            "storage_class": spec.get("storageClassName"),
            "volume_name": spec.get("volumeName"),
            "access_modes": spec.get("accessModes", []),
        })
    return out


# ============================================================
# KUBERNETES DIAGNOSTICS — Wave 1 (P1)
# ============================================================


@mcp.tool()
def kube_resource_audit(ns: str = "") -> list[dict]:
    """Audit CPU/memory requests vs limits for all pods. Flags pods with no limits,
    no requests, or overcommitted resources."""
    if ns:
        _validate_k8s_name(ns, "namespace")
    ns_flag = f"-n {ns}" if ns else "--all-namespaces"
    raw = _kube(f"get pods {ns_flag} -o json", timeout=30)
    data = _json.loads(raw)
    results = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        phase = item.get("status", {}).get("phase", "")
        if phase not in ("Running", "Pending"):
            continue
        for c in item.get("spec", {}).get("containers", []):
            res = c.get("resources", {})
            req = res.get("requests", {})
            lim = res.get("limits", {})
            warnings = []
            if not req:
                warnings.append("no_requests")
            if not lim:
                warnings.append("no_limits")
            results.append({
                "namespace": meta.get("namespace"),
                "pod": meta.get("name"),
                "container": c.get("name"),
                "cpu_request": req.get("cpu"),
                "cpu_limit": lim.get("cpu"),
                "memory_request": req.get("memory"),
                "memory_limit": lim.get("memory"),
                "warnings": warnings,
            })
    return results


@mcp.tool()
def kube_oom_events(ns: str = "", last: str = "1h") -> list[dict]:
    """Find OOMKilled containers across the cluster in the last N hours."""
    if ns:
        _validate_k8s_name(ns, "namespace")
    if last:
        _validate_duration(last)
    ns_flag = f"-n {ns}" if ns else "--all-namespaces"
    raw = _kube(f"get pods {ns_flag} -o json", timeout=30)
    data = _json.loads(raw)
    oom = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        for cs in item.get("status", {}).get("containerStatuses", []):
            last_state = cs.get("lastState", {}).get("terminated", {})
            if last_state.get("reason") == "OOMKilled":
                oom.append({
                    "namespace": meta.get("namespace"),
                    "pod": meta.get("name"),
                    "container": cs.get("name"),
                    "exit_code": last_state.get("exitCode"),
                    "finished_at": last_state.get("finishedAt"),
                    "restart_count": cs.get("restartCount", 0),
                })
    return oom


@mcp.tool()
def kube_crashloop_pods(threshold: int = 5) -> list[dict]:
    """Find pods with restart count above threshold, with last restart reason."""
    raw = _kube("get pods --all-namespaces -o json", timeout=30)
    data = _json.loads(raw)
    crashers = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        for cs in item.get("status", {}).get("containerStatuses", []):
            rc = cs.get("restartCount", 0)
            if rc > threshold:
                last_state = cs.get("lastState", {}).get("terminated", {})
                crashers.append({
                    "namespace": meta.get("namespace"),
                    "pod": meta.get("name"),
                    "container": cs.get("name"),
                    "restarts": rc,
                    "last_reason": last_state.get("reason", "Unknown"),
                    "last_exit_code": last_state.get("exitCode"),
                    "last_finished": last_state.get("finishedAt"),
                })
    crashers.sort(key=lambda x: x["restarts"], reverse=True)
    return crashers


@mcp.tool()
def kube_previous_logs(name: str, ns: str = "default",
                       container: str = "", tail: int = 200) -> str:
    """Get logs from the PREVIOUS container incarnation (pre-crash). Critical for
    diagnosing OOMKill and CrashLoopBackOff."""
    _validate_k8s_name(name, "pod name")
    _validate_k8s_name(ns, "namespace")
    if container:
        _validate_k8s_name(container, "container")
    cmd = f"logs {name} -n {ns} --previous --tail={int(tail)}"
    if container:
        cmd += f" -c {container}"
    return _kube(cmd)


@mcp.tool()
def kube_init_container_logs(name: str, ns: str = "default") -> str:
    """Get logs from all init containers of a pod. Init container failures are
    invisible in normal ``kube_logs`` calls."""
    _validate_k8s_name(name, "pod name")
    _validate_k8s_name(ns, "namespace")
    raw = _kube(f"get pod {name} -n {ns} -o json", timeout=15)
    pod = _json.loads(raw)
    init_containers = pod.get("spec", {}).get("initContainers", [])
    if not init_containers:
        return "No init containers in this pod."
    logs = []
    for ic in init_containers:
        ic_name = ic.get("name", "unknown")
        try:
            log = _kube(f"logs {name} -n {ns} -c {ic_name} --tail=100")
            logs.append(f"=== {ic_name} ===\n{log}")
        except RuntimeError as e:
            logs.append(f"=== {ic_name} === ERROR: {e}")
    return "\n\n".join(logs)


@mcp.tool()
def kube_netpol_list(ns: str = "") -> list[dict]:
    """List all NetworkPolicies with parsed ingress/egress rules."""
    if ns:
        _validate_k8s_name(ns, "namespace")
    ns_flag = f"-n {ns}" if ns else "--all-namespaces"
    raw = _kube(f"get networkpolicy {ns_flag} -o json", timeout=20)
    data = _json.loads(raw)
    policies = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        spec = item.get("spec", {})
        policies.append({
            "namespace": meta.get("namespace"),
            "name": meta.get("name"),
            "pod_selector": spec.get("podSelector", {}),
            "policy_types": spec.get("policyTypes", []),
            "ingress_rules": len(spec.get("ingress", [])),
            "egress_rules": len(spec.get("egress", [])),
        })
    return policies


@mcp.tool()
def kube_ingress_validate() -> list[dict]:
    """Cross-check every Ingress: host → service → endpoints → running pod.
    Returns issues found (empty list = all healthy)."""
    raw = _kube("get ingress --all-namespaces -o json", timeout=20)
    data = _json.loads(raw)
    issues = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        ns = meta.get("namespace", "default")
        for rule in item.get("spec", {}).get("rules", []):
            host = rule.get("host", "")
            for path in rule.get("http", {}).get("paths", []):
                svc_name = path.get("backend", {}).get("service", {}).get("name", "")
                svc_port = path.get("backend", {}).get("service", {}).get("port", {})
                if not svc_name:
                    continue
                # Check service exists
                try:
                    svc_raw = _kube(f"get service {svc_name} -n {ns} -o json", timeout=10)
                    _json.loads(svc_raw)
                except RuntimeError:
                    issues.append({
                        "host": host, "service": svc_name, "namespace": ns,
                        "issue": "service_not_found",
                    })
                    continue
                # Check endpoints
                try:
                    ep_raw = _kube(f"get endpoints {svc_name} -n {ns} -o json", timeout=10)
                    ep = _json.loads(ep_raw)
                    addresses = []
                    for subset in ep.get("subsets", []):
                        addresses.extend(subset.get("addresses", []))
                    if not addresses:
                        issues.append({
                            "host": host, "service": svc_name, "namespace": ns,
                            "issue": "no_ready_endpoints",
                        })
                except RuntimeError:
                    issues.append({
                        "host": host, "service": svc_name, "namespace": ns,
                        "issue": "endpoints_error",
                    })
    return issues


@mcp.tool()
def kube_service_endpoints(ns: str = "") -> list[dict]:
    """Find services with zero ready endpoints (broken service wiring)."""
    if ns:
        _validate_k8s_name(ns, "namespace")
    ns_flag = f"-n {ns}" if ns else "--all-namespaces"
    raw = _kube(f"get endpoints {ns_flag} -o json", timeout=20)
    data = _json.loads(raw)
    empty = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        name = meta.get("name", "")
        # Skip k8s internal services
        if name in ("kubernetes", "kube-dns"):
            continue
        subsets = item.get("subsets", [])
        total_addresses = sum(len(s.get("addresses", [])) for s in subsets)
        if total_addresses == 0:
            empty.append({
                "namespace": meta.get("namespace"),
                "service": name,
                "ready_endpoints": 0,
            })
    return empty


@mcp.tool()
def dns_completeness_check() -> dict:
    """Compare all Ingress hosts vs Cloudflare DNS records. Returns missing records."""
    # Get all ingress hosts
    raw = _kube("get ingress --all-namespaces -o json", timeout=20)
    data = _json.loads(raw)
    ingress_hosts = set()
    for item in data.get("items", []):
        for rule in item.get("spec", {}).get("rules", []):
            host = rule.get("host", "")
            if host:
                ingress_hosts.add(host)
    # Get Cloudflare DNS records (uses CF_DEFAULT_ZONE).
    zone = cf_default_zone()
    domain_suffix = "." + zone
    try:
        cf_records = cf_dns_list(zone=zone)
        cf_names = {r.get("name") for r in cf_records.get("records", [])}
    except Exception as e:
        return {"error": f"Failed to query Cloudflare: {e}", "ingress_hosts": sorted(ingress_hosts)}

    missing = sorted(ingress_hosts - cf_names)
    extra = sorted({n for n in cf_names if n.endswith(domain_suffix)} - ingress_hosts)
    return {
        "ingress_hosts": sorted(ingress_hosts),
        "dns_records": sorted(n for n in cf_names if n.endswith(domain_suffix)),
        "missing_dns": missing,
        "extra_dns": extra,
        "all_complete": len(missing) == 0,
    }


@mcp.tool()
def kube_orphan_cleanup_preview() -> dict:
    """Preview orphan resources: stale ReplicaSets (0 replicas), completed/failed Jobs,
    evicted pods. Read-only — returns counts only, does not delete."""
    # Stale ReplicaSets
    raw = _kube("get rs --all-namespaces -o json", timeout=30)
    rs_data = _json.loads(raw)
    stale_rs = [
        f"{item['metadata']['namespace']}/{item['metadata']['name']}"
        for item in rs_data.get("items", [])
        if item.get("spec", {}).get("replicas", 1) == 0
    ]
    # Completed/Failed Jobs
    raw = _kube("get jobs --all-namespaces -o json", timeout=20)
    job_data = _json.loads(raw)
    old_jobs = []
    for item in job_data.get("items", []):
        status = item.get("status", {})
        if status.get("succeeded") or status.get("failed"):
            old_jobs.append(
                f"{item['metadata']['namespace']}/{item['metadata']['name']}"
            )
    # Evicted pods
    raw = _kube("get pods --all-namespaces --field-selector=status.phase=Failed -o json", timeout=20)
    pod_data = _json.loads(raw)
    evicted = [
        f"{item['metadata']['namespace']}/{item['metadata']['name']}"
        for item in pod_data.get("items", [])
        if item.get("status", {}).get("reason") == "Evicted"
    ]
    return {
        "stale_replicasets": len(stale_rs),
        "stale_rs_sample": stale_rs[:10],
        "completed_jobs": len(old_jobs),
        "completed_jobs_sample": old_jobs[:10],
        "evicted_pods": len(evicted),
        "evicted_pods_sample": evicted[:10],
    }


@mcp.tool()
def host_mount_status() -> list[dict]:
    """Check all mount points: mounted/stale/offline status. Detects NFS issues
    and unmounted external drives."""
    output = _ssh_exec("mount | grep -vE 'tmpfs|proc|sys|cgroup|devpts|securityfs|debugfs|snap|fuse'")
    mounts = []
    for line in output.split("\n"):
        parts = line.split()
        if len(parts) >= 6:
            device = parts[0]
            mount_point = parts[2]
            fs_type = parts[4]
            mounts.append({
                "device": device,
                "mount_point": mount_point,
                "fs_type": fs_type,
                "status": "mounted",
            })
    # Check expected mounts
    expected = ["/media/nas", "/media/external_drive", "/mnt/internal_drive"]
    mounted_paths = {m["mount_point"] for m in mounts}
    for exp in expected:
        if exp not in mounted_paths:
            mounts.append({
                "device": "?",
                "mount_point": exp,
                "fs_type": "?",
                "status": "NOT_MOUNTED",
            })
    return mounts


@mcp.tool()
def host_reboot_required() -> dict:
    """Check if the host needs a reboot (kernel update, etc.)."""
    try:
        result = _ssh_exec("test -f /var/run/reboot-required && cat /var/run/reboot-required || echo 'NO'")
        required = result.strip() != "NO"
        return {
            "reboot_required": required,
            "message": result.strip() if required else "No reboot required",
        }
    except RuntimeError as e:
        return {"reboot_required": False, "error": str(e)}


# ============================================================
# ENRICHMENT & INTELLIGENCE — Wave 2 (P2)
# ============================================================


@mcp.tool()
def kube_log_errors(name: str, ns: str = "default", tail: int = 500,
                    pattern: str = "ERROR|FATAL|PANIC|Exception") -> list[dict]:
    """Scan pod logs for error patterns with dedup and count."""
    _validate_k8s_name(name, "pod name")
    _validate_k8s_name(ns, "namespace")
    # Validate pattern — only allow safe regex chars
    if not _re.match(r'^[a-zA-Z0-9|_ .-]+$', pattern):
        raise ValueError(f"Invalid pattern: {pattern!r}")
    raw = _kube(f"logs {name} -n {ns} --tail={int(tail)}")
    errors = {}
    for line in raw.split("\n"):
        if _re.search(pattern, line, _re.IGNORECASE):
            # Dedup by first 80 chars
            key = line.strip()[:80]
            if key in errors:
                errors[key]["count"] += 1
            else:
                errors[key] = {"line": line.strip(), "count": 1}
    return sorted(errors.values(), key=lambda x: x["count"], reverse=True)[:50]


@mcp.tool()
def host_os_version() -> dict:
    """Get Ubuntu release, kernel, microk8s, and snap versions."""
    cmd = (
        "echo OS:$(lsb_release -ds 2>/dev/null) ; "
        "echo KERNEL:$(uname -r) ; "
        "echo MICROK8S:$(snap list microk8s 2>/dev/null | tail -1 | awk '{print $2}') ; "
        "echo DOCKER:$(docker --version 2>/dev/null | head -1)"
    )
    output = _ssh_exec(cmd)
    result = {}
    for line in output.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip().lower()] = val.strip()
    return result


@mcp.tool()
def backup_status() -> dict:
    """Check restic backup timer status, last run time, and result."""
    timer = _ssh_exec("systemctl status restic-backup.timer 2>/dev/null | head -6")
    journal = _ssh_exec("journalctl -u restic-backup.service --no-pager -n 5 2>/dev/null")
    return {"timer": timer, "last_runs": journal}


@mcp.tool()
def backup_snapshots(last: int = 10) -> list[dict]:
    """List restic backup snapshots with dates and sizes."""
    try:
        raw = _ssh_exec(
            f"restic snapshots --json --last {int(last)} 2>/dev/null",
            timeout=30,
        )
        snapshots = _json.loads(raw) if raw.strip().startswith("[") else []
        return [
            {
                "id": s.get("short_id"),
                "time": s.get("time"),
                "hostname": s.get("hostname"),
                "paths": s.get("paths", []),
            }
            for s in snapshots
        ]
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
def netdata_query(chart: str, after: int = -600, before: int = 0,
                  points: int = 10) -> dict:
    """Query Netdata API for any metric chart. Uses HTTP directly (no SSH).

    Args:
        chart: Netdata chart name (e.g. 'system.cpu', 'system.ram', 'disk_space._')
        after: Seconds relative to now (negative = past). Default -600 (10 min ago).
        before: Seconds relative to now. Default 0 (now).
        points: Number of data points. Default 10.
    """
    # Validate chart name
    if not chart or not _re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._/-]*$', chart) or len(chart) > 100:
        raise ValueError(f"Invalid chart name: {chart!r}")
    import httpx
    try:
        r = httpx.get(
            f"http://netdata.default.svc:19999/api/v1/data",
            params={"chart": chart, "after": after, "before": before, "points": points,
                    "format": "json"},
            timeout=15.0,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e), "chart": chart}


@mcp.tool()
def ansible_inventory() -> dict:
    """List Ansible inventory hosts and groups (read-only)."""
    try:
        raw = _ssh_exec("cat ~/src/homelab/ansible/inventories/home/hosts.yml 2>/dev/null || "
                        "cat ~/src/homelab/ansible/inventories/home/hosts 2>/dev/null || "
                        "echo 'inventory not found'")
        return {"raw": raw}
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
def ansible_playbook_list() -> list[dict]:
    """List available Ansible playbooks with descriptions."""
    try:
        raw = _ssh_exec("ls ~/src/homelab/ansible/playbooks/*.yml 2>/dev/null")
        playbooks = []
        for line in raw.split("\n"):
            name = line.strip().split("/")[-1] if line.strip() else ""
            if name:
                # Try to get first comment line as description
                try:
                    desc = _ssh_exec(f"head -3 {line.strip()} 2>/dev/null | grep '#' | head -1")
                    desc = desc.strip().lstrip("#").strip()
                except RuntimeError:
                    desc = ""
                playbooks.append({"name": name, "path": line.strip(), "description": desc})
        return playbooks
    except RuntimeError as e:
        return [{"error": str(e)}]


@mcp.tool()
def homebridge_log_errors(tail: int = 200) -> list[dict]:
    """Parse Homebridge container logs for ERROR/WARN patterns with dedup."""
    try:
        raw = _kube("logs -l app=homebridge -n home-automation --tail=" + str(int(tail)))
        errors = {}
        for line in raw.split("\n"):
            if _re.search(r"ERROR|WARN|error|warn|fail", line, _re.IGNORECASE):
                key = line.strip()[:100]
                if key in errors:
                    errors[key]["count"] += 1
                else:
                    errors[key] = {"line": line.strip(), "count": 1}
        return sorted(errors.values(), key=lambda x: x["count"], reverse=True)[:30]
    except RuntimeError as e:
        return [{"error": str(e)}]


@mcp.tool()
def kube_top_pods(ns: str = "", sort: str = "memory") -> list[dict]:
    """Get actual resource usage per pod (requires metrics-server).
    Sort by 'memory' or 'cpu'."""
    if ns:
        _validate_k8s_name(ns, "namespace")
    if sort not in ("memory", "cpu"):
        raise ValueError(f"sort must be 'memory' or 'cpu', got {sort!r}")
    ns_flag = f"-n {ns}" if ns else "--all-namespaces"
    try:
        raw = _kube(f"top pods {ns_flag} --sort-by={sort} --no-headers", timeout=15)
        pods = []
        for line in raw.split("\n"):
            parts = line.split()
            if len(parts) >= 3:
                # all-namespaces: NS NAME CPU MEM
                # single ns: NAME CPU MEM
                if ns:
                    pods.append({"name": parts[0], "cpu": parts[1], "memory": parts[2]})
                else:
                    pods.append({"namespace": parts[0], "name": parts[1],
                                 "cpu": parts[2], "memory": parts[3] if len(parts) > 3 else "?"})
        return pods
    except RuntimeError as e:
        if "metrics" in str(e).lower() or "not available" in str(e).lower():
            return [{"error": "Metrics API not available. Enable metrics-server addon: sudo microk8s enable metrics-server"}]
        return [{"error": str(e)}]


# ============================================================
# OS LOGS — Wave 3 (P3)
# ============================================================


@mcp.tool()
def host_syslog_errors(hours: int = 4, level: str = "err,crit,alert") -> list[dict]:
    """Parse syslog for errors in the last N hours."""
    if not _re.match(r'^[a-z,]+$', level):
        raise ValueError(f"Invalid level: {level!r}")
    raw = _ssh_exec(f"journalctl --priority={level} --since='{int(hours)} hours ago' --no-pager -n 100 2>/dev/null")
    entries = []
    for line in raw.split("\n"):
        if line.strip():
            entries.append({"line": line.strip()})
    return entries[:100]


@mcp.tool()
def host_dmesg_errors(level: str = "err,crit,alert") -> list[dict]:
    """Get hardware errors from dmesg (disk, USB, memory issues)."""
    if not _re.match(r'^[a-z,]+$', level):
        raise ValueError(f"Invalid level: {level!r}")
    try:
        raw = _ssh_exec(f"sudo dmesg --level={level} 2>/dev/null | tail -50")
        return [{"line": line.strip()} for line in raw.split("\n") if line.strip()]
    except RuntimeError as e:
        return [{"error": str(e)}]


@mcp.tool()
def host_auth_log(tail: int = 50) -> list[dict]:
    """Recent SSH logins and failed auth attempts."""
    try:
        raw = _ssh_exec(f"sudo tail -n {int(tail)} /var/log/auth.log 2>/dev/null")
        return [{"line": line.strip()} for line in raw.split("\n") if line.strip()]
    except RuntimeError as e:
        return [{"error": str(e)}]


# ============================================================
# AUDIT LOG INSPECTION (read-only)
# ============================================================

# Allow-list of tool names for the optional filter.
_AUDIT_TOOL_RE = _re.compile(r'^[a-z][a-z0-9_]{1,63}$')


@mcp.tool()
def audit_tail(n: int = 50, tool: str = "") -> dict:
    """Return the last ``n`` lines of the MCP audit log, optionally filtered by tool name.

    Args:
        n: How many lines to return (capped at 1000 to bound memory).
        tool: Optional exact tool-name filter (regex injection is rejected).
    """
    if not isinstance(n, int) or n < 1:
        raise ValueError("n must be a positive int")
    if n > 1000:
        n = 1000
    if tool and not _AUDIT_TOOL_RE.match(tool):
        raise ValueError(f"Invalid tool filter: {tool!r}")

    if not _AUDIT_LOG_PATH.exists():
        return {"path": str(_AUDIT_LOG_PATH), "exists": False, "lines": []}

    # Read the whole file and slice — audit logs are bounded by logrotate.
    with open(_AUDIT_LOG_PATH, encoding="utf-8", errors="replace") as fh:
        all_lines = fh.readlines()

    lines: list[dict] = []
    for raw in all_lines[-(n * 4):]:  # over-read so post-filter still has n
        parts = raw.rstrip("\n").split("\t")
        if len(parts) < 4:
            continue
        ts, name, params, status = parts[0], parts[1], parts[2], parts[3]
        if tool and name != tool:
            continue
        lines.append({"ts": ts, "tool": name, "params": params, "status": status})

    return {
        "path": str(_AUDIT_LOG_PATH),
        "exists": True,
        "count": len(lines[-n:]),
        "lines": lines[-n:],
    }


def main():
    mcp.run()


if __name__ == "__main__":
    main()
