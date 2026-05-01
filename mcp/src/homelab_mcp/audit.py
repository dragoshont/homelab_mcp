"""Audit logging primitives for homelab MCP tool calls."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path


AUDIT_LOGGER_NAME = "homelab_mcp.audit"
AUDIT_HANDLER_PATH_ATTR = "_homelab_mcp_audit_path"


def configure_audit_logger(path: Path) -> logging.Logger:
    """Configure the audit logger once for the requested path."""
    path = path.expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    logger.setLevel(logging.INFO)

    audit_path = str(path)
    for existing_handler in list(logger.handlers):
        existing_path = getattr(existing_handler, AUDIT_HANDLER_PATH_ATTR, None)
        if existing_path is not None and existing_path != audit_path:
            logger.removeHandler(existing_handler)
            existing_handler.close()

    if not any(
        getattr(handler, AUDIT_HANDLER_PATH_ATTR, None) == audit_path
        for handler in logger.handlers
    ):
        audit_handler = logging.FileHandler(path, encoding="utf-8")
        audit_handler.setFormatter(logging.Formatter("%(message)s"))
        setattr(audit_handler, AUDIT_HANDLER_PATH_ATTR, audit_path)
        logger.addHandler(audit_handler)

    return logger


def audit(logger: logging.Logger, tool_name: str, params: dict, result_summary: str = "ok") -> None:
    """Log a tool call to the audit file."""
    ts = datetime.now(timezone.utc).isoformat()
    logger.info(f"{ts}\t{tool_name}\t{params}\t{result_summary}")