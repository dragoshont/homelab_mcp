"""Stable environment helpers for the homelab MCP runtime.

Environment-driven configuration contract
-----------------------------------------

This module is the single place where the MCP reads operator-specific
configuration from environment variables. The principle: nothing about a
specific homelab (hostnames, IPs, usernames, DNS zones, ingress domains)
may be hard-coded in source. Source must be public-publishable; per-deployment
values come from env (set by the K8s Deployment manifest, .env, or shell).

Required env vars (no default; raise on use if unset):

    HOMELAB_HOST              SSH host for kube/host/flux/ansible/backup tools.
    HOMELAB_SSH_USER          SSH username for the same tools.
    HOMELAB_INGRESS_DOMAIN    Apex domain for ingress hostnames; the
                              ingress_probe SSRF guard regex is built as
                              ^[a-z0-9][a-z0-9-]*\\.<DOMAIN>$.
    HOMELAB_INGRESS_IP        IPv4 of the ingress controller; used to
                              `--resolve` curl/openssl probes to the
                              cluster path even when public DNS does not
                              point at it.
    CF_ALLOWED_ZONES          Comma-separated DNS zones the cf_dns_*
                              tools may read or write. Empty/unset means
                              cf_dns_* tools refuse all zones.
    CF_DEFAULT_ZONE           Default zone used when cf_dns_list/get is
                              called without an explicit zone.

Optional env vars (with documented default):

    HOMELAB_SSH_KEY           Path to SSH private key. Empty (default)
                              uses the agent's default identity.
    HOMELAB_MCP_AUDIT_LOG     Audit log path. Default ~/homelab-mcp/audit.log.
    HOMELAB_MCP_READONLY      Block mutating tools when truthy. Default false.

Helper functions defined here are the ONLY way other modules should read
these vars. Tests in test_settings_env_contract.py pin this contract.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


class ConfigurationError(RuntimeError):
    """Raised when a required env var is missing or unparseable.

    The message MUST include the env var name and a short remediation hint
    so the operator can fix the deployment without reading source.
    """


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable using the server's existing rules.

    Strips surrounding whitespace before comparison so that values pasted in
    from YAML or a `.env` file (where trailing spaces are easy to introduce)
    do not silently become False.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes")


def audit_log_path() -> Path:
    """Return the configured MCP audit log path.

    Empty-string env (e.g. HOMELAB_MCP_AUDIT_LOG="") falls back to the default
    rather than producing Path("") which resolves to the current directory and
    then fails with IsADirectoryError when opened as a file.

    BUG-009 fix: expanduser is applied to the operator-supplied value too,
    so HOMELAB_MCP_AUDIT_LOG="~/logs/audit.log" expands to the user's home
    instead of being written as a literal "~"-prefixed path.
    """
    raw = os.environ.get("HOMELAB_MCP_AUDIT_LOG", "").strip()
    if not raw:
        raw = "~/homelab-mcp/audit.log"
    return Path(os.path.expanduser(raw))


def require_env(name: str, hint: str = "") -> str:
    """Read a required env var, raising ConfigurationError if missing/blank.

    The error message includes the env var name and an optional hint so the
    operator gets a deterministic, public-safe error rather than a stack
    trace at first tool invocation.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        suffix = f" — {hint}" if hint else ""
        raise ConfigurationError(
            f"Required env var {name!r} is not set or empty{suffix}"
        )
    return value


def homelab_ingress_domain() -> str:
    """Apex domain used for the ingress SSRF allowlist.

    The ingress_probe tool refuses any host that does not match
    ^[a-z0-9][a-z0-9-]*\\.<this domain>$.
    """
    return require_env(
        "HOMELAB_INGRESS_DOMAIN",
        hint="set to your homelab ingress apex (e.g., 'cluster.example')",
    )


# Subdomain label per RFC 1035: starts with letter/digit, may contain letters,
# digits, hyphens, but MUST NOT end with a hyphen. Public-safe pattern: the
# domain comes from env at call time, never compiled from a literal.
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def ingress_host_re() -> re.Pattern[str]:
    """Compile the ingress allowlist regex from the env-supplied domain.

    The subdomain label conforms to RFC 1035: leading character is a letter
    or digit; trailing character must also be a letter or digit (single-char
    labels permitted). Trailing hyphens are rejected.

    Compiled fresh on each call (cheap; the pattern is short). This avoids
    a module-level cache whose contents would be wrong if the env var
    changed between calls (it shouldn't in production, but tests rely on
    being able to set/unset the env var per-test).
    """
    domain = homelab_ingress_domain()
    if not _DOMAIN_RE.match(domain):
        raise ConfigurationError(
            f"HOMELAB_INGRESS_DOMAIN={domain!r} is not a valid domain "
            f"(letters, digits, hyphens, dots only)"
        )
    return re.compile(rf"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.{re.escape(domain)}$")


_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


def homelab_ingress_ip() -> str:
    """IPv4 address of the ingress controller for curl/openssl --resolve."""
    value = require_env(
        "HOMELAB_INGRESS_IP",
        hint="set to your ingress controller's IPv4 (e.g., '203.0.113.5')",
    )
    if not _IPV4_RE.match(value):
        raise ConfigurationError(
            f"HOMELAB_INGRESS_IP={value!r} is not a valid IPv4 address"
        )
    return value


_IPV4_RE = re.compile(
    r"^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$"
)


def cf_allowed_zones() -> frozenset[str]:
    """Cloudflare zones the cf_dns_* tools may read or write.

    Returns a frozenset (deterministic, hashable). Empty set if the env var
    is unset OR empty after parsing — callers MUST treat empty as "no zones
    allowed" and refuse the operation.
    """
    raw = os.environ.get("CF_ALLOWED_ZONES", "").strip()
    if not raw:
        return frozenset()
    zones = [z.strip().lower() for z in raw.split(",") if z.strip()]
    for z in zones:
        if not _DOMAIN_RE.match(z):
            raise ConfigurationError(
                f"CF_ALLOWED_ZONES contains invalid zone {z!r} "
                f"(letters, digits, hyphens, dots only)"
            )
    return frozenset(zones)


def cf_default_zone() -> str:
    """Default zone used when cf_dns_list/get is called without an explicit zone.

    Falls back to the FIRST entry of CF_ALLOWED_ZONES (sorted for determinism)
    if CF_DEFAULT_ZONE is not explicitly set. Raises if neither is configured
    so cf_dns_* tools fail-fast with a clear message rather than silently
    operating on an unintended zone.

    If both are set, the explicit CF_DEFAULT_ZONE MUST be a member of
    CF_ALLOWED_ZONES — otherwise the operator's allowlist could be silently
    bypassed by a default zone they thought was advisory.
    """
    explicit = os.environ.get("CF_DEFAULT_ZONE", "").strip().lower()
    allowed = cf_allowed_zones()
    if explicit:
        if not _DOMAIN_RE.match(explicit):
            raise ConfigurationError(
                f"CF_DEFAULT_ZONE={explicit!r} is not a valid domain"
            )
        # Membership check MUST run regardless of whether `allowed` is empty.
        # An empty allowlist with an explicit default would otherwise create
        # a silent bypass: the operator's intent in setting CF_ALLOWED_ZONES
        # to empty ("refuse all zones") is contradicted by returning a
        # CF_DEFAULT_ZONE that no zone-validation step would accept.
        if explicit not in allowed:
            raise ConfigurationError(
                f"CF_DEFAULT_ZONE={explicit!r} is not in CF_ALLOWED_ZONES "
                f"({sorted(allowed) if allowed else 'empty'}); the default "
                f"cannot bypass the allowlist"
            )
        return explicit
    if not allowed:
        raise ConfigurationError(
            "Neither CF_DEFAULT_ZONE nor CF_ALLOWED_ZONES is set; "
            "cf_dns_* tools cannot infer a zone"
        )
    return sorted(allowed)[0]
