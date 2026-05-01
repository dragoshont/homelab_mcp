"""Tests for AUD-1: audit log status column distinguishes ok vs rejected_readonly."""
import importlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fresh_server(monkeypatch, tmp_path):
    """Reload server module with a clean audit log path and configurable readonly."""
    audit_log = tmp_path / "audit.log"
    monkeypatch.setenv("HOMELAB_MCP_AUDIT_LOG", str(audit_log))

    def _reload(readonly: bool):
        if readonly:
            monkeypatch.setenv("HOMELAB_MCP_READONLY", "true")
        else:
            monkeypatch.delenv("HOMELAB_MCP_READONLY", raising=False)
        from conftest import reload_server_facade  # type: ignore[import]
        return reload_server_facade(), audit_log

    return _reload


def _last_line(p: Path) -> str:
    return p.read_text(encoding="utf-8").splitlines()[-1]


def test_rejected_call_emits_rejected_readonly_status(fresh_server):
    """MP-1: blocking a write tool MUST emit '\\trejected_readonly' in audit.log."""
    m, audit = fresh_server(readonly=True)
    with patch.object(m, "_qbt", return_value=MagicMock()):
        with pytest.raises(RuntimeError, match="readonly mode"):
            m.qbt_pause("all")
    line = _last_line(audit)
    assert "\trejected_readonly" in line, f"missing rejection status in: {line!r}"


def test_successful_call_emits_ok_status(fresh_server):
    """MP-2: a successful write call (readonly off) MUST emit '\\tok' suffix."""
    m, audit = fresh_server(readonly=False)
    fake_qbt = MagicMock()
    with patch.object(m, "_qbt", return_value=fake_qbt):
        m.qbt_pause("all")
    line = _last_line(audit)
    assert line.endswith("\tok"), f"expected '\\tok' suffix, got: {line!r}"


def test_audit_default_status_is_ok(fresh_server):
    """MP-4: _audit(name, args) without status MUST default to 'ok'."""
    m, audit = fresh_server(readonly=False)
    m._audit("manual_test", {"k": "v"})
    line = _last_line(audit)
    assert line.endswith("\tok"), f"default status should be 'ok', got: {line!r}"
    assert "rejected_readonly" not in line


def test_audit_line_is_tab_separated_4_columns(fresh_server):
    """MP-5: audit lines MUST split into exactly 4 TAB-separated columns.

    BUG-008 fix: params is JSON-encoded with no whitespace separators,
    so user-supplied tabs/newlines can no longer split a record into
    extra columns or extra lines (log forging guard).
    """
    m, audit = fresh_server(readonly=False)
    m._audit("col_test", {"a": 1}, "ok")
    line = _last_line(audit)
    cols = line.split("\t")
    assert len(cols) == 4, f"expected 4 columns, got {len(cols)}: {cols}"
    ts, name, args, status = cols
    assert "T" in ts, f"col[0] should be ISO timestamp: {ts!r}"
    assert name == "col_test"
    assert args == '{"a":1}'
    assert status == "ok"

# --- BUG-008 regression tests ---


def test_audit_line_resists_log_forging_via_newline(fresh_server):
    """BUG-008: a newline in a param value must NOT split the record."""
    m, audit = fresh_server(readonly=False)
    m._audit("forge_test", {"evil": "row1\nfake_ts\tfake_tool\tfake_args\tok"}, "ok")
    line = _last_line(audit)
    cols = line.split("\t")
    assert len(cols) == 4, f"newline-injected record splits row: {cols}"
    # Status field at the end must still be the literal "ok" we passed in.
    assert cols[3] == "ok"


def test_audit_line_resists_log_forging_via_tab(fresh_server):
    """BUG-008: a tab in a param value must NOT split the columns."""
    m, audit = fresh_server(readonly=False)
    m._audit("forge_test_tab", {"evil": "tabbed\tvalue"}, "ok")
    line = _last_line(audit)
    cols = line.split("\t")
    assert len(cols) == 4, f"tab-injected param splits row: {cols}"


def test_audit_status_strips_control_chars(fresh_server):
    """BUG-008: tabs/newlines in result_summary are sanitised before write."""
    m, audit = fresh_server(readonly=False)
    m._audit("status_test", {}, "ok\textra\nrow")
    line = _last_line(audit)
    cols = line.split("\t")
    assert len(cols) == 4, f"status-injected control chars split row: {cols}"
    assert "\n" not in cols[3]
    assert "\t" not in cols[3]
