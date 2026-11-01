"""Trusted, optional integration configuration for the lightweight voice agent.

Environment configuration is deliberately separate from browser room claims.
An enabled MCP server requires an explicit grant list and bearer token.
Hermes runs are API-owned; they never impersonate CLI/TUI sessions.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no", ""}


def _enabled(value: str, name: str) -> bool:
    value = value.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name} must be true or false")


def _validate_url(value: str, label: str) -> str:
    parsed = urlsplit(value)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in {"https", "http"}
    ):
        raise ValueError(f"{label} must be a valid HTTP(S) URL without embedded credentials")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"{label} must use HTTPS outside loopback")
    return value.rstrip("/")


@dataclass(frozen=True)
class MCPConfig:
    url: str
    token: str = field(repr=False)
    allowed_tools: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_url(self.url, "MCP_URL")
        if not self.token or not self.token.strip():
            raise ValueError("MCP_TOKEN is required when MCP is enabled")
        if not self.allowed_tools:
            raise ValueError("MCP_ALLOWED_TOOLS must explicitly grant at least one tool")
        if any(not _TOOL_NAME.fullmatch(name) or name == "*" for name in self.allowed_tools):
            raise ValueError("MCP_ALLOWED_TOOLS contains an invalid name")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("MCP_ALLOWED_TOOLS contains duplicate names")


@dataclass(frozen=True)
class HermesConfig:
    url: str
    key: str = field(repr=False)

    def __post_init__(self) -> None:
        _validate_url(self.url, "HERMES_API_URL")
        if not self.key or not self.key.strip():
            raise ValueError("HERMES_API_KEY is required when delegation is enabled")


@dataclass(frozen=True)
class Integrations:
    mcp: MCPConfig | None = None
    hermes: HermesConfig | None = None


def load_integrations(env: dict[str, str] | None = None) -> Integrations:
    e = env if env is not None else os.environ
    mcp_url = e.get("MCP_URL", "").strip()
    mcp = None
    if mcp_url:
        names = tuple(s.strip() for s in e.get("MCP_ALLOWED_TOOLS", "").split(",") if s.strip())
        mcp = MCPConfig(url=mcp_url, token=e.get("MCP_TOKEN", ""), allowed_tools=names)
    elif e.get("MCP_ALLOWED_TOOLS", "").strip() or e.get("MCP_TOKEN", "").strip():
        raise ValueError("MCP_URL must be set when MCP credentials or tools are configured")

    hermes_enabled = _enabled(e.get("HERMES_DELEGATION_ENABLED", "false"), "HERMES_DELEGATION_ENABLED")
    hermes = None
    if hermes_enabled:
        hermes = HermesConfig(
            url=e.get("HERMES_API_URL", "http://127.0.0.1:8642").strip(),
            key=e.get("HERMES_API_KEY", ""),
        )
    return Integrations(mcp=mcp, hermes=hermes)
