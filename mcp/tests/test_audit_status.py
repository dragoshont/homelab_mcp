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
        sys.modules.pop("homelab_mcp.server", None)
        return importlib.import_module("homelab_mcp.server"), audit_log

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
    """MP-5: audit lines MUST split into exactly 4 TAB-separated columns."""
    m, audit = fresh_server(readonly=False)
    m._audit("col_test", {"a": 1}, "ok")
    line = _last_line(audit)
    cols = line.split("\t")
    assert len(cols) == 4, f"expected 4 columns, got {len(cols)}: {cols}"
    ts, name, args, status = cols
    assert "T" in ts, f"col[0] should be ISO timestamp: {ts!r}"
    assert name == "col_test"
    assert args == "{'a': 1}"
    assert status == "ok"
