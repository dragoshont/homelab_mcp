"""Phase 0.4: pin the env-driven configuration contract.

These tests fail if anyone hardcodes a homelab-specific value back into the
source, regresses the env-var error messages, or forgets to validate input
formats. They are the safety net for "source is public-publishable".
"""
from __future__ import annotations

import re

import pytest

from homelab_mcp.settings import (
    ConfigurationError,
    cf_allowed_zones,
    cf_default_zone,
    homelab_ingress_domain,
    homelab_ingress_ip,
    ingress_host_re,
    require_env,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Strip the env vars we test so each test starts from a clean slate."""
    for name in (
        "HOMELAB_HOST",
        "HOMELAB_SSH_USER",
        "HOMELAB_INGRESS_DOMAIN",
        "HOMELAB_INGRESS_IP",
        "HOMELAB_MCP_AUDIT_LOG",
        "HOMELAB_MCP_READONLY",
        "CF_ALLOWED_ZONES",
        "CF_DEFAULT_ZONE",
        "TEST_VAR",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# require_env
# ---------------------------------------------------------------------------

def test_require_env_missing_raises_with_name():
    with pytest.raises(ConfigurationError) as exc:
        require_env("TEST_VAR")
    assert "TEST_VAR" in str(exc.value)


def test_require_env_blank_raises():
    import os
    os.environ["TEST_VAR"] = "   "
    with pytest.raises(ConfigurationError):
        require_env("TEST_VAR")


def test_require_env_returns_stripped_value(monkeypatch):
    monkeypatch.setenv("TEST_VAR", "  hello  ")
    assert require_env("TEST_VAR") == "hello"


def test_require_env_includes_hint_in_error_message():
    with pytest.raises(ConfigurationError) as exc:
        require_env("TEST_VAR", hint="set this to your foo")
    assert "set this to your foo" in str(exc.value)


# ---------------------------------------------------------------------------
# homelab_ingress_domain / ingress_host_re
# ---------------------------------------------------------------------------

def test_homelab_ingress_domain_missing_raises():
    with pytest.raises(ConfigurationError) as exc:
        homelab_ingress_domain()
    assert "HOMELAB_INGRESS_DOMAIN" in str(exc.value)


def test_homelab_ingress_domain_returns_value(monkeypatch):
    monkeypatch.setenv("HOMELAB_INGRESS_DOMAIN", "cluster.example")
    assert homelab_ingress_domain() == "cluster.example"


def test_ingress_host_re_built_from_env(monkeypatch):
    monkeypatch.setenv("HOMELAB_INGRESS_DOMAIN", "cluster.example")
    pat = ingress_host_re()
    assert pat.match("sonarr.cluster.example")
    assert pat.match("a.cluster.example")
    assert not pat.match("evil.com")
    assert not pat.match("sonarr.evil.com")
    assert not pat.match(".cluster.example")  # empty subdomain
    assert not pat.match("sonarr.cluster.example.attacker.com")


def test_ingress_host_re_rejects_invalid_domain(monkeypatch):
    monkeypatch.setenv("HOMELAB_INGRESS_DOMAIN", "not_a_domain!")
    with pytest.raises(ConfigurationError):
        ingress_host_re()


def test_ingress_host_re_escapes_dots_in_domain(monkeypatch):
    """If we forgot to re.escape the env value, '.' would match any char,
    so 'sonarr.clusterXexample' (X where the literal dot should be) would
    slip past the domain 'cluster.example' separator."""
    monkeypatch.setenv("HOMELAB_INGRESS_DOMAIN", "cluster.example")
    pat = ingress_host_re()
    # The dot between 'cluster' and 'example' MUST be a literal dot.
    # Without re.escape, an X (or any single char) would match the unescaped ".".
    assert not pat.match("sonarr.clusterXexample")
    # Sanity: the correct dotted form still matches.
    assert pat.match("sonarr.cluster.example")


def test_ingress_host_re_rejects_trailing_hyphen(monkeypatch):
    """RFC 1035: a label MUST NOT end with a hyphen. The pattern must enforce this."""
    monkeypatch.setenv("HOMELAB_INGRESS_DOMAIN", "cluster.example")
    pat = ingress_host_re()
    assert not pat.match("foo-.cluster.example")
    assert not pat.match("-foo.cluster.example")  # leading hyphen also invalid per existing pattern
    assert pat.match("foo-bar.cluster.example")  # interior hyphen OK
    assert pat.match("a.cluster.example")  # single-char label OK


# ---------------------------------------------------------------------------
# homelab_ingress_ip
# ---------------------------------------------------------------------------

def test_homelab_ingress_ip_missing_raises():
    with pytest.raises(ConfigurationError) as exc:
        homelab_ingress_ip()
    assert "HOMELAB_INGRESS_IP" in str(exc.value)


def test_homelab_ingress_ip_valid(monkeypatch):
    monkeypatch.setenv("HOMELAB_INGRESS_IP", "10.0.0.5")
    assert homelab_ingress_ip() == "10.0.0.5"


@pytest.mark.parametrize("bad", [
    "256.0.0.1",
    "10.0.0",
    "10.0.0.0.1",
    "not.an.ip",
    "10.0.0.1; rm -rf /",  # injection attempt
])
def test_homelab_ingress_ip_invalid_rejected(monkeypatch, bad):
    monkeypatch.setenv("HOMELAB_INGRESS_IP", bad)
    with pytest.raises(ConfigurationError):
        homelab_ingress_ip()


# ---------------------------------------------------------------------------
# cf_allowed_zones / cf_default_zone
# ---------------------------------------------------------------------------

def test_cf_allowed_zones_unset_returns_empty_set():
    assert cf_allowed_zones() == frozenset()


def test_cf_allowed_zones_parses_csv(monkeypatch):
    monkeypatch.setenv("CF_ALLOWED_ZONES", "example.com, another.example, third.test")
    assert cf_allowed_zones() == frozenset({"example.com", "another.example", "third.test"})


def test_cf_allowed_zones_lowercases(monkeypatch):
    monkeypatch.setenv("CF_ALLOWED_ZONES", "Example.COM")
    assert cf_allowed_zones() == frozenset({"example.com"})


def test_cf_allowed_zones_invalid_zone_raises(monkeypatch):
    monkeypatch.setenv("CF_ALLOWED_ZONES", "not_a_zone!")
    with pytest.raises(ConfigurationError):
        cf_allowed_zones()


def test_cf_default_zone_explicit_wins(monkeypatch):
    monkeypatch.setenv("CF_ALLOWED_ZONES", "a.example, b.example")
    monkeypatch.setenv("CF_DEFAULT_ZONE", "b.example")
    assert cf_default_zone() == "b.example"


def test_cf_default_zone_falls_back_to_first_allowed(monkeypatch):
    monkeypatch.setenv("CF_ALLOWED_ZONES", "z.example, a.example, m.example")
    # Sorted ⇒ a.example.
    assert cf_default_zone() == "a.example"


def test_cf_default_zone_explicit_must_be_in_allowed(monkeypatch):
    """If CF_ALLOWED_ZONES is set, an explicit CF_DEFAULT_ZONE that is NOT
    in the allowlist must be rejected, otherwise the default could silently
    bypass the allowlist contract."""
    monkeypatch.setenv("CF_ALLOWED_ZONES", "internal.example")
    monkeypatch.setenv("CF_DEFAULT_ZONE", "prod.example")
    with pytest.raises(ConfigurationError) as exc:
        cf_default_zone()
    assert "not in CF_ALLOWED_ZONES" in str(exc.value)


def test_cf_default_zone_explicit_in_allowed_works(monkeypatch):
    monkeypatch.setenv("CF_ALLOWED_ZONES", "internal.example, prod.example")
    monkeypatch.setenv("CF_DEFAULT_ZONE", "prod.example")
    assert cf_default_zone() == "prod.example"


def test_cf_default_zone_explicit_with_empty_allowed_raises(monkeypatch):
    """Adversarial finding ADV-008 critical: CF_DEFAULT_ZONE set but
    CF_ALLOWED_ZONES unset or empty must NOT silently bypass the allowlist.
    Empty allowlist means 'refuse all zones'; an explicit default cannot
    create a single-zone bypass.
    """
    monkeypatch.setenv("CF_DEFAULT_ZONE", "prod.example")
    monkeypatch.delenv("CF_ALLOWED_ZONES", raising=False)
    with pytest.raises(ConfigurationError) as exc:
        cf_default_zone()
    assert "not in CF_ALLOWED_ZONES" in str(exc.value)


def test_cf_default_zone_explicit_with_empty_string_allowed_raises(monkeypatch):
    """Same as above but with CF_ALLOWED_ZONES='' explicitly."""
    monkeypatch.setenv("CF_DEFAULT_ZONE", "prod.example")
    monkeypatch.setenv("CF_ALLOWED_ZONES", "")
    with pytest.raises(ConfigurationError):
        cf_default_zone()


def test_audit_log_path_empty_env_falls_back_to_default(monkeypatch):
    """Adversarial finding ADV-004 high: HOMELAB_MCP_AUDIT_LOG='' should
    fall back to the default ~/homelab-mcp/audit.log, not Path('').
    """
    from homelab_mcp.settings import audit_log_path
    monkeypatch.setenv("HOMELAB_MCP_AUDIT_LOG", "")
    p = audit_log_path()
    assert str(p) != ""
    assert str(p) != "."
    assert "homelab-mcp" in str(p) or "audit" in str(p).lower()


def test_audit_log_path_whitespace_only_env_falls_back(monkeypatch):
    from homelab_mcp.settings import audit_log_path
    monkeypatch.setenv("HOMELAB_MCP_AUDIT_LOG", "   ")
    p = audit_log_path()
    assert str(p).strip() not in ("", ".")
    assert "homelab-mcp" in str(p) or "audit" in str(p).lower()


def test_audit_log_path_explicit_value_honored(monkeypatch, tmp_path):
    from homelab_mcp.settings import audit_log_path
    target = tmp_path / "custom-audit.log"
    monkeypatch.setenv("HOMELAB_MCP_AUDIT_LOG", str(target))
    assert audit_log_path() == target


def test_audit_log_path_expands_tilde_in_operator_value(monkeypatch):
    """BUG-009: ~ in the operator-supplied path must be expanded.

    Previously only the fallback path went through expanduser(); a
    ``HOMELAB_MCP_AUDIT_LOG=~/logs/audit.log`` was wrapped as
    ``Path("~/logs/audit.log")`` literally and then failed at open()
    because no directory called "~" existed.
    """
    import os
    from homelab_mcp.settings import audit_log_path
    monkeypatch.setenv("HOMELAB_MCP_AUDIT_LOG", "~/logs/audit.log")
    p = audit_log_path()
    assert "~" not in str(p), f"path should not contain literal ~: {p}"
    assert str(p).startswith(os.path.expanduser("~"))


def test_env_flag_strips_whitespace(monkeypatch):
    """YAML and .env files easily introduce trailing spaces; truthy intent
    must not silently flip to False because of whitespace."""
    from homelab_mcp.settings import env_flag
    monkeypatch.setenv("HOMELAB_MCP_READONLY", "true ")
    assert env_flag("HOMELAB_MCP_READONLY") is True
    monkeypatch.setenv("HOMELAB_MCP_READONLY", "  yes")
    assert env_flag("HOMELAB_MCP_READONLY") is True
    monkeypatch.setenv("HOMELAB_MCP_READONLY", " 1\n")
    assert env_flag("HOMELAB_MCP_READONLY") is True


def test_cf_default_zone_neither_set_raises():
    with pytest.raises(ConfigurationError) as exc:
        cf_default_zone()
    assert "CF_DEFAULT_ZONE" in str(exc.value) or "CF_ALLOWED_ZONES" in str(exc.value)


def test_cf_default_zone_invalid_explicit_raises(monkeypatch):
    monkeypatch.setenv("CF_DEFAULT_ZONE", "not_a_domain!")
    with pytest.raises(ConfigurationError):
        cf_default_zone()
