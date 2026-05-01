"""Architecture guard tests for the MCP module split.

These tests intentionally avoid importing ``homelab_mcp.server`` so they can run
before optional MCP runtime dependencies are available locally. Runtime tests in
``test_readonly_enforcement.py`` and ``test_audit_status.py`` remain the stronger
contract when the full dependency environment is installed.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "homelab_mcp"
SERVER = PACKAGE_ROOT / "server.py"
AUDIT = PACKAGE_ROOT / "audit.py"
POLICY = PACKAGE_ROOT / "policy.py"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _reset_logger_handlers(logger: logging.Logger) -> None:
    """Detach + close every handler before clearing the list.

    BUG-011 fix: a plain ``logger.handlers.clear()`` drops references to the
    FileHandler objects without closing the underlying file descriptors.
    On Windows that keeps a tempfile lock alive across test cases and
    occasionally trips ``PermissionError: [WinError 32]`` on tmp_path
    teardown. Always close-then-remove.
    """
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()


EXPECTED_TOOL_NAMES = {
    "ansible_inventory",
    "ansible_playbook_list",
    "apple_apps",
    "apple_devices",
    "apple_launch_app",
    "apple_now_playing",
    "apple_play_pause",
    "apple_remote",
    "apple_run_shortcut",
    "apple_scan",
    "apple_volume",
    "audit_tail",
    "backup_snapshots",
    "backup_status",
    "cert_manager_status",
    "cf_dns_get",
    "cf_dns_list",
    "dirigera_blinds",
    "dirigera_devices",
    "dirigera_lights",
    "dirigera_outlets",
    "dirigera_scenes",
    "dirigera_sensors",
    "dirigera_set_blind",
    "dirigera_set_light",
    "dirigera_set_outlet",
    "dirigera_status",
    "dirigera_trigger_scene",
    "dns_completeness_check",
    "flux_diff",
    "flux_reconcile",
    "flux_resume",
    "flux_status",
    "flux_suspend",
    "gitops_app_inventory",
    "gitops_drift",
    "gitops_secret_audit",
    "homebridge_accessories",
    "homebridge_log_errors",
    "homebridge_plugins",
    "homebridge_status",
    "homelab_overview",
    "host_auth_log",
    "host_disk",
    "host_dmesg_errors",
    "host_failed_units",
    "host_journal",
    "host_mount_status",
    "host_nfs_status",
    "host_os_version",
    "host_packages_upgradable",
    "host_reboot_required",
    "host_security_audit",
    "host_services",
    "host_smart",
    "host_status",
    "host_syslog_errors",
    "image_compare_tags",
    "image_inspect",
    "image_list_tags",
    "ingress_probe",
    "kube_crashloop_pods",
    "kube_describe",
    "kube_events",
    "kube_image_can_pull",
    "kube_image_present",
    "kube_ingress_validate",
    "kube_init_container_logs",
    "kube_log_errors",
    "kube_logs",
    "kube_netpol_list",
    "kube_oom_events",
    "kube_orphan_cleanup_preview",
    "kube_pods",
    "kube_previous_logs",
    "kube_pvc_usage",
    "kube_resource_audit",
    "kube_restart",
    "kube_rollout_status",
    "kube_service_endpoints",
    "kube_top_pods",
    "lidarr_health",
    "lidarr_missing",
    "lidarr_search_missing",
    "media_disk_pressure",
    "media_indexer_health",
    "media_pipeline_health",
    "mylar3_health",
    "mylar3_missing",
    "mylar3_search_missing",
    "mylar3_series",
    "netdata_query",
    "plex_libraries",
    "plex_maintenance",
    "plex_recent",
    "plex_scan_library",
    "plex_status",
    "prowlarr_add_torznab_indexer",
    "prowlarr_health",
    "prowlarr_remove_indexer",
    "prowlarr_search",
    "prowlarr_test_indexers",
    "qbt_pause",
    "qbt_resume",
    "qbt_status",
    "qbt_torrents",
    "radarr_calendar",
    "radarr_health",
    "radarr_missing",
    "radarr_movies",
    "radarr_queue",
    "radarr_search_missing",
    "readarr_health",
    "readarr_missing",
    "readarr_search_missing",
    "scrypted_status",
    "sonarr_calendar",
    "sonarr_health",
    "sonarr_missing",
    "sonarr_queue",
    "sonarr_search_missing",
    "sonarr_series",
    "unifi_block",
    "unifi_client",
    "unifi_clients",
    "unifi_devices",
    "unifi_health",
    "unifi_port_forwards",
    "unifi_reconnect",
    "unifi_top_talkers",
    "unifi_unblock",
    "unifi_wlan_set",
    "unifi_wlans",
}

UPSTREAM_CLIENT_CONSTRUCTORS = {
    "HomebridgeClient",
    "Mylar3Client",
    "PlexClient",
    "QbtClient",
    "ServarrClient",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _package_python_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _decorator_name(decorator: ast.expr) -> str:
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    if isinstance(decorator, ast.Name):
        return decorator.id
    return ""


def _tool_names_from_package() -> set[str]:
    names: set[str] = set()
    for path in _package_python_files():
        module = _parse(path)
        for node in ast.walk(module):
            if isinstance(node, ast.FunctionDef):
                if any(_decorator_name(decorator) == "tool" for decorator in node.decorator_list):
                    names.add(node.name)
    return names


def test_public_tool_inventory_is_stable():
    """The module split must not add, drop, or rename public MCP tools."""
    actual = _tool_names_from_package()
    assert actual == EXPECTED_TOOL_NAMES
    assert len(actual) == 133


def test_only_one_fastmcp_homelab_constructor_exists():
    """Domain modules must not create independent FastMCP app instances."""
    constructors: list[str] = []
    for path in _package_python_files():
        module = _parse(path)
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "FastMCP":
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            if node.args[0].value == "homelab":
                constructors.append(str(path.relative_to(PACKAGE_ROOT)))

    assert constructors == ["app.py"]


def test_upstream_clients_are_not_constructed_at_module_import_time():
    """Lazy client creation must remain inside call-time functions only."""
    violations: list[str] = []
    for path in _package_python_files():
        module = _parse(path)
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    if child.func.id in UPSTREAM_CLIENT_CONSTRUCTORS:
                        violations.append(f"{path.relative_to(PACKAGE_ROOT)}:{child.lineno}:{child.func.id}")

    assert violations == []


def test_configure_audit_logger_does_not_duplicate_handlers(tmp_path):
    """Repeated audit setup for the same path must not add duplicate handlers."""
    from homelab_mcp.audit import AUDIT_LOGGER_NAME, configure_audit_logger

    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    _reset_logger_handlers(logger)
    log_path = tmp_path / "audit.log"

    first = configure_audit_logger(log_path)
    handler_count = len(first.handlers)
    second = configure_audit_logger(log_path)

    assert first is second
    assert len(second.handlers) == handler_count


def test_configure_audit_logger_preserves_unmanaged_handlers(tmp_path):
    """External audit sinks must not be removed by file handler setup."""
    from homelab_mcp.audit import AUDIT_LOGGER_NAME, configure_audit_logger

    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    _reset_logger_handlers(logger)
    external_handler = logging.StreamHandler()
    logger.addHandler(external_handler)

    configure_audit_logger(tmp_path / "audit.log")

    assert external_handler in logger.handlers


def test_configure_audit_logger_disables_propagation(tmp_path):
    """Audit logger must NOT propagate to ancestor handlers (BUG-007).

    Audit records are an append-only sink; if the root logger has a stderr
    handler installed by the operator, propagation duplicates every audit
    line into stderr and desyncs from the on-disk audit file. The handler
    on AUDIT_LOGGER_NAME is the canonical destination, full stop.
    """
    from homelab_mcp.audit import AUDIT_LOGGER_NAME, configure_audit_logger

    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    _reset_logger_handlers(logger)
    logger.propagate = True  # Simulate any prior state.

    configure_audit_logger(tmp_path / "audit.log")

    assert logger.propagate is False


def test_configure_audit_logger_rebinds_relative_path_after_cwd_change(tmp_path, monkeypatch):
    """Relative audit paths must compare by effective absolute location."""
    from homelab_mcp.audit import AUDIT_LOGGER_NAME, configure_audit_logger

    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    _reset_logger_handlers(logger)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()

    monkeypatch.chdir(first_dir)
    first = configure_audit_logger(Path("audit.log"))
    first_paths = [getattr(handler, "baseFilename", "") for handler in first.handlers]

    monkeypatch.chdir(second_dir)
    second = configure_audit_logger(Path("audit.log"))
    second_paths = [getattr(handler, "baseFilename", "") for handler in second.handlers]

    assert first is second
    assert str(first_dir / "audit.log") in first_paths
    assert str(second_dir / "audit.log") in second_paths
    assert str(first_dir / "audit.log") not in second_paths


def test_readonly_error_message_preserves_legacy_dash():
    """The extracted policy must preserve the existing readonly error text."""
    from homelab_mcp.policy import check_readonly

    try:
        check_readonly("kube_restart", readonly=True, audit=lambda *_args: None)
    except RuntimeError as exc:
        assert "\u2014 server is in readonly mode" in str(exc)
    else:
        raise AssertionError("expected readonly RuntimeError")


def test_policy_write_tools_are_extracted_without_changing_server_compatibility():
    """policy.WRITE_TOOLS is the single source of truth for mutating tools.

    Phase 1.0: server.py is a thin orchestrator. The legacy
    ``_WRITE_TOOLS = _POLICY_WRITE_TOOLS`` alias now lives in
    _runtime.py. The contract is unchanged: somewhere in the package a
    module imports ``WRITE_TOOLS`` from ``policy`` and re-exposes it as
    ``_WRITE_TOOLS`` for backward compatibility.
    """
    policy = _parse(POLICY)
    policy_names = {
        target.id
        for node in policy.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "WRITE_TOOLS" in policy_names
    runtime = (PACKAGE_ROOT / "_runtime.py").read_text(encoding="utf-8")
    assert "from homelab_mcp.policy import WRITE_TOOLS as _POLICY_WRITE_TOOLS" in runtime
    assert "_WRITE_TOOLS = _POLICY_WRITE_TOOLS" in runtime


def test_console_script_keeps_server_main_compatibility():
    """The public console script still resolves through the compatible server module."""
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    server = _parse(SERVER)
    functions = {node.name for node in server.body if isinstance(node, ast.FunctionDef)}
    assert 'homelab-mcp = "homelab_mcp.server:main"' in pyproject
    assert "main" in functions
# --- BUG-006 regression test ---


def test_dockerfile_sets_home_before_user(_=None):
    """BUG-006: HOME must be set before USER 1000:1000 so SSH ~/.ssh resolves."""
    import re
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    home_match = re.search(r"^ENV\s+HOME=", dockerfile, re.MULTILINE)
    user_match = re.search(r"^USER\s+1000", dockerfile, re.MULTILINE)
    assert home_match is not None, "Dockerfile must declare ENV HOME=..."
    assert user_match is not None, "Dockerfile must declare USER 1000:..."
    assert home_match.start() < user_match.start(), \
        "ENV HOME=... must appear BEFORE USER 1000:1000 (BUG-006)"

# --- Phase 1.0 split structure regression tests ---


def test_phase_1_0_server_is_thin_orchestrator():
    """Phase 1.0 contract MP-6: server.py < 80 lines, no @mcp.tool decorators.

    Detects regression where a tool gets re-added to server.py during a
    domain-extraction phase. server.py must remain a thin orchestrator
    that imports _runtime + each tools/{domain} module.
    """
    server = (PACKAGE_ROOT / "server.py").read_text(encoding="utf-8")
    line_count = server.count("\n") + 1
    assert line_count < 80, (
        f"server.py is {line_count} lines; Phase 1.0 contract caps it at 80. "
        f"If you need to add code, put it in _runtime.py or tools/<domain>.py."
    )
    tree = ast.parse(server)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                assert "mcp.tool" not in ast.unparse(dec), (
                    f"server.py defines a tool ({node.name!r}); tools must "
                    f"live in tools/<domain>.py per Phase 1.0 contract."
                )


def test_phase_1_0_tools_package_has_five_domain_modules():
    """Phase 1.0 contract MP-5: every domain tool module is self-contained."""
    tools_dir = PACKAGE_ROOT / "tools"
    assert tools_dir.is_dir(), "mcp/src/homelab_mcp/tools/ must exist"
    expected = {"platform.py", "media.py", "network.py", "homeauto.py",
                "control.py", "__init__.py"}
    actual = {p.name for p in tools_dir.iterdir() if p.is_file()}
    missing = expected - actual
    assert not missing, f"tools/ missing required modules: {sorted(missing)}"


def test_phase_1_0_tool_count_per_domain_matches_inventory():
    """Phase 1.0 contract MP-1: each domain module's tool count matches inventory."""
    import json
    inv_path = Path(__file__).resolve().parents[2] / "docs" / "migration" / "tool-inventory.json"
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    expected_counts: dict[str, int] = {}
    for entry in inv["tools"]:
        full = entry["server"]
        assert full.startswith("homelab-mcp-"), full
        domain = full[len("homelab-mcp-"):]
        expected_counts[domain] = expected_counts.get(domain, 0) + 1

    for domain, expected in expected_counts.items():
        path = PACKAGE_ROOT / "tools" / f"{domain}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        actual = sum(
            1
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any("mcp.tool" in ast.unparse(d) for d in node.decorator_list)
        )
        assert actual == expected, (
            f"tools/{domain}.py has {actual} @mcp.tool functions but the "
            f"inventory expects {expected} for that domain."
        )
