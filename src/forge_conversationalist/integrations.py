"""Trusted, optional integration configuration for the lightweight voice agent.

Environment configuration is deliberately separate from browser room claims.
An enabled MCP server requires an explicit grant list and bearer token.
Hermes runs are API-owned; they never impersonate CLI/TUI sessions.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .embodiment import EmbodimentConfig
from .ensemble import PuppeteerEnsembleConfig

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no", ""}
_REACHY_REVISION = re.compile(r"^sha256:[0-9a-f]{64}$")
_RESTRICTED_REACHY_TOOLS = {
    "play_move", "device_info", "device_state", "list_assets", "action_lookup", "device_stop"
}
_CONVERSATION_REACHY_TOOLS = frozenset({
    "play_move", "list_assets", "action_lookup", "device_stop"
})


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
class PuppeteerStdioConfig:
    """A narrowly scoped, operator-owned Forge Puppeteer Reachy gateway."""

    config_path: Path
    allowed_tools: tuple[str, ...]
    command: str = "forge-puppeteer-mcp"
    approved_moves: tuple[tuple[str, str], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.config_path.is_absolute() or not self.config_path.is_file():
            raise ValueError("PUPPETEER_MCP_CONFIG must point to an existing absolute JSON file")
        if self.command != "forge-puppeteer-mcp":
            executable = Path(self.command)
            if (
                not executable.is_absolute()
                or executable.name != "forge-puppeteer-mcp"
                or not executable.is_file()
                or not os.access(executable, os.X_OK)
            ):
                raise ValueError(
                    "PUPPETEER_MCP_COMMAND must be forge-puppeteer-mcp or an executable absolute path with that basename"
                )
        # These four tools provide pinned discovery, action, outcome lookup, and stop.
        if (
            len(self.allowed_tools) != len(_CONVERSATION_REACHY_TOOLS)
            or len(set(self.allowed_tools)) != len(self.allowed_tools)
            or set(self.allowed_tools) != _CONVERSATION_REACHY_TOOLS
        ):
            raise ValueError(
                "PUPPETEER_MCP_ALLOWED_TOOLS must explicitly grant play_move, list_assets, action_lookup, and device_stop"
            )
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("PUPPETEER_MCP_CONFIG must be readable valid JSON") from exc
        if not isinstance(config, dict):
            raise TypeError("PUPPETEER_MCP_CONFIG must contain a JSON object")
        if (
            config.get("backend") != "reachy_sdk"
            or config.get("device") != "reachy"
            or config.get("restricted_actions") is not True
            or config.get("physical_authority") != "I_AUTHORIZE_REACHY_CONTROL"
        ):
            raise ValueError("PUPPETEER_MCP_CONFIG must explicitly authorize restricted Reachy moves")
        ownership_db = config.get("ownership_db")
        action_journal = config.get("action_journal")
        if not isinstance(ownership_db, str) or not isinstance(action_journal, str):
            raise TypeError(
                "PUPPETEER_MCP_CONFIG must set shared absolute ownership_db and action_journal paths"
            )
        ownership_path = Path(ownership_db)
        journal_path = Path(action_journal)
        if (
            not ownership_path.is_absolute()
            or not journal_path.is_absolute()
            or ownership_path.resolve() == journal_path.resolve()
        ):
            raise ValueError(
                "PUPPETEER_MCP_CONFIG ownership_db and action_journal must be distinct absolute paths"
            )
        grants = config.get("allowed_tools")
        if (
            not isinstance(grants, list)
            or any(not isinstance(name, str) for name in grants)
            or not set(self.allowed_tools) <= set(grants)
            or len(set(grants)) != len(grants)
            or not set(grants) <= _RESTRICTED_REACHY_TOOLS
        ):
            raise ValueError("PUPPETEER_MCP_CONFIG has unsafe or invalid restricted Reachy tool grants")
        moves = config.get("approved_moves")
        if not isinstance(moves, list) or not moves:
            raise ValueError("PUPPETEER_MCP_CONFIG must pin at least one approved Reachy move")
        pins: set[tuple[str, str]] = set()
        for move in moves:
            if not isinstance(move, dict):
                raise TypeError("PUPPETEER_MCP_CONFIG contains an invalid approved move pin")
            asset_id, revision = move.get("asset_id"), move.get("revision")
            if (
                not isinstance(asset_id, str) or not asset_id.startswith("reachy/")
                or asset_id == "reachy/" or not isinstance(revision, str)
                or not _REACHY_REVISION.fullmatch(revision)
            ):
                raise ValueError("PUPPETEER_MCP_CONFIG contains an invalid approved move pin")
            pin = (asset_id, revision)
            if pin in pins:
                raise ValueError("PUPPETEER_MCP_CONFIG contains duplicate approved move pins")
            pins.add(pin)
        object.__setattr__(self, "approved_moves", tuple(sorted(pins)))


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
    puppeteer_stdio: PuppeteerStdioConfig | None = None
    embodiment: EmbodimentConfig | None = None
    ensemble: PuppeteerEnsembleConfig | None = None
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

    puppeteer_path = e.get("PUPPETEER_MCP_CONFIG", "").strip()
    puppeteer_command = e.get("PUPPETEER_MCP_COMMAND", "").strip() or "forge-puppeteer-mcp"
    puppeteer_tools = tuple(
        item.strip() for item in e.get("PUPPETEER_MCP_ALLOWED_TOOLS", "").split(",")
        if item.strip()
    )
    puppeteer_stdio = None
    if puppeteer_path:
        puppeteer_stdio = PuppeteerStdioConfig(
            Path(puppeteer_path), puppeteer_tools, command=puppeteer_command
        )
    elif puppeteer_tools or puppeteer_command != "forge-puppeteer-mcp":
        raise ValueError("PUPPETEER_MCP_CONFIG must be set when Puppeteer options are configured")

    embodiment_path = e.get("PUPPETEER_EMBODIMENT_CONFIG", "").strip()
    embodiment_command = e.get("PUPPETEER_EMBODIMENT_COMMAND", "").strip() or "forge-puppeteer-mcp"
    embodiment = None
    if embodiment_path:
        path = Path(embodiment_path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("PUPPETEER_EMBODIMENT_CONFIG is invalid") from exc
        if not isinstance(data, dict):
            raise ValueError("PUPPETEER_EMBODIMENT_CONFIG must be an object")
        embodiment = EmbodimentConfig(
            path, performer_id=data.get("instance_id", ""), command=embodiment_command
        )
    elif embodiment_command != "forge-puppeteer-mcp":
        raise ValueError("PUPPETEER_EMBODIMENT_CONFIG is required with its command")

    ensemble_path = e.get("PUPPETEER_ENSEMBLE_CONFIG", "").strip()
    ensemble_command = e.get("PUPPETEER_ENSEMBLE_COMMAND", "").strip() or "forge-puppeteer-ensemble"
    ensemble = None
    if ensemble_path:
        if puppeteer_stdio is not None or embodiment is not None:
            raise ValueError(
                "A room cannot start the native ensemble alongside another Puppeteer controller"
            )
        ensemble = PuppeteerEnsembleConfig(Path(ensemble_path), command=ensemble_command)
    elif ensemble_command != "forge-puppeteer-ensemble":
        raise ValueError("PUPPETEER_ENSEMBLE_CONFIG is required with its command")

    hermes_enabled = _enabled(e.get("HERMES_DELEGATION_ENABLED", "false"), "HERMES_DELEGATION_ENABLED")
    hermes = None
    if hermes_enabled:
        hermes = HermesConfig(
            url=e.get("HERMES_API_URL", "http://127.0.0.1:8642").strip(),
            key=e.get("HERMES_API_KEY", ""),
        )
    return Integrations(mcp=mcp, puppeteer_stdio=puppeteer_stdio,
                        embodiment=embodiment, ensemble=ensemble, hermes=hermes)
