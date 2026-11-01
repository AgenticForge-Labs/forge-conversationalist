"""No real MCP server, Hermes gateway or robot is used by these tests."""

import json
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from forge_conversationalist.agent import _room_instructions
from forge_conversationalist.assistant_tools import build_assistant_tools
from forge_conversationalist.integrations import (
    HermesConfig,
    Integrations,
    MCPConfig,
    load_integrations,
)


def test_integrations_default_to_off() -> None:
    assert load_integrations({}) == Integrations()
    assert build_assistant_tools(Integrations()) == []


def test_mcp_requires_explicit_names_and_backend_credential() -> None:
    settings = {
        "MCP_URL": "http://127.0.0.1:4444/mcp",
        "MCP_TOKEN": "only-server-knows",
        "MCP_ALLOWED_TOOLS": "read_status, get_details",
    }
    conf = load_integrations(settings).mcp
    assert conf is not None
    assert conf.allowed_tools == ("read_status", "get_details")
    assert "only-server-knows" not in repr(conf)

    for setting in ("MCP_TOKEN", "MCP_ALLOWED_TOOLS"):
        with pytest.raises(ValueError):
            load_integrations({**settings, setting: ""})
    with pytest.raises(ValueError):
        load_integrations({"MCP_ALLOWED_TOOLS": "read_status"})


@pytest.mark.parametrize(
    "invalid",
    ["*", "good,*", "good,good", "../motion", "bad tool", "command/exec"],
)
def test_mcp_tool_allowlist_rejects_wildcards_ambiguous_names(invalid: str) -> None:
    with pytest.raises(ValueError):
        load_integrations({
            "MCP_URL": "http://localhost:4444/mcp",
            "MCP_TOKEN": "token",
            "MCP_ALLOWED_TOOLS": invalid,
        })


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example/mcp",
        "http://user:pw@localhost:4444/mcp",
        "https://example.org/mcp?token=wrong",
        "file:///tmp/mcp",
        "https://example.org/mcp#fragment",
    ],
)
def test_mcp_disallows_unsafe_urls(url: str) -> None:
    with pytest.raises(ValueError):
        MCPConfig(url=url, token="secret", allowed_tools=("read_status",))


def test_hermes_remains_off_unless_explicitly_enabled() -> None:
    assert load_integrations({"HERMES_API_KEY": "secret"}).hermes is None
    with pytest.raises(ValueError):
        load_integrations({"HERMES_DELEGATION_ENABLED": "maybe"})
    with pytest.raises(ValueError):
        load_integrations({"HERMES_DELEGATION_ENABLED": "true"})
    config = load_integrations({
        "HERMES_DELEGATION_ENABLED": "true",
        "HERMES_API_KEY": "very-private",
    }).hermes
    assert config is not None
    assert config.url == "http://127.0.0.1:8642"
    assert "very-private" not in repr(config)
    with pytest.raises(FrozenInstanceError):
        config.url = "https://other.example"


def test_allows_https_only_remote_api_endpoints() -> None:
    assert HermesConfig(url="https://agents.example.org", key="secret").url
    with pytest.raises(ValueError):
        HermesConfig(url="http://agents.example.org", key="secret")


def test_native_mcp_toolset_receives_exact_whitelist_and_auth_only() -> None:
    observed = {}
    def server(url: str, **kwargs: object) -> tuple[str, str]:
        observed.update(kwargs)
        return ("server", url)
    def toolset(**kwargs: object) -> object:
        observed["toolset"] = kwargs
        return ("toolset", kwargs)
    fake_mcp = SimpleNamespace(MCPServerHTTP=server, MCPToolset=toolset)
    opts = Integrations(
        mcp=MCPConfig(url="http://127.0.0.1:4444/mcp",
                      token="private-jwt", allowed_tools=("read_status",))
    )
    tools = build_assistant_tools(opts, mcp_module=fake_mcp)
    assert len(tools) == 1
    assert observed["transport_type"] == "streamable_http"
    assert observed["allowed_tools"] == ["read_status"]
    assert observed["headers"] == {"Authorization": "Bearer private-jwt"}
    assert observed["toolset"]["id"] == "approved-mcp"


def reachy_config(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "backend": "reachy_sdk",
        "device": "reachy",
        "physical_authority": "I_AUTHORIZE_REACHY_CONTROL",
        "restricted_actions": True,
        "allowed_tools": ["device_info", "device_state", "list_assets", "action_lookup", "play_move", "device_stop"],
        "ownership_db": "/tmp/forge-puppeteer/ownership.sqlite3",
        "action_journal": "/tmp/forge-puppeteer/actions.sqlite3",
        "approved_moves": [{"asset_id": "reachy/curious", "revision": "sha256:" + "a" * 64}],
    }
    return values | overrides


def write_reachy_config(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "reachy-mcp.json"
    path.write_text(json.dumps(reachy_config(**overrides)), encoding="utf-8")
    return path


def test_puppeteer_stdio_requires_explicit_pinned_restricted_profile(tmp_path: Path) -> None:
    path = write_reachy_config(tmp_path)
    config = load_integrations({
        "PUPPETEER_MCP_CONFIG": str(path),
        "PUPPETEER_MCP_ALLOWED_TOOLS": "play_move,list_assets,action_lookup,device_stop",
    }).puppeteer_stdio
    assert config is not None
    assert set(config.allowed_tools) == {"play_move", "list_assets", "action_lookup", "device_stop"}
    assert config.approved_moves == (("reachy/curious", "sha256:" + "a" * 64),)
    instructions = _room_instructions(
        Integrations(puppeteer_stdio=config), caller_id="person-test", session_id="room-test"
    )
    assert "available for explicit user-requested" in instructions
    assert "Call list_assets to retrieve the gateway-filtered approved moves" in instructions
    assert "reachy/curious (sha256:" + "a" * 64 + ")" in instructions
    assert "caller_id=person-test" in instructions
    assert "session_id=room-test" in instructions
    assert "Never move Reachy automatically" in instructions

    invalid_configs = [
        {"restricted_actions": False},
        {"physical_authority": None},
        {"backend": "fake"},
        {"device": "stackchan"},
        {"allowed_tools": ["play_move", "head_pose"]},
        {"ownership_db": None},
        {"action_journal": "relative/actions.sqlite3"},
        {"action_journal": "/tmp/forge-puppeteer/ownership.sqlite3"},
        {"approved_moves": []},
        {"approved_moves": [{"asset_id": "reachy/curious", "revision": "stale"}]},
    ]
    for invalid in invalid_configs:
        with pytest.raises(ValueError):
            load_integrations({
                "PUPPETEER_MCP_CONFIG": str(write_reachy_config(tmp_path, **invalid)),
                "PUPPETEER_MCP_ALLOWED_TOOLS": "play_move,list_assets",
            })

    with pytest.raises(ValueError, match="must explicitly grant"):
        load_integrations({
            "PUPPETEER_MCP_CONFIG": str(path),
            "PUPPETEER_MCP_ALLOWED_TOOLS": "play_move,head_pose",
        })
    with pytest.raises(ValueError, match="must explicitly grant"):
        load_integrations({
            "PUPPETEER_MCP_CONFIG": str(path),
            "PUPPETEER_MCP_ALLOWED_TOOLS": "play_move,list_assets,action_lookup",
        })
    with pytest.raises(ValueError, match="must be set"):
        load_integrations({"PUPPETEER_MCP_ALLOWED_TOOLS": "play_move"})


def test_puppeteer_stdio_constructs_fixed_launcher_without_credentials(tmp_path: Path, monkeypatch) -> None:
    observed: dict[str, object] = {}

    def server(**kwargs: object) -> object:
        observed.update(kwargs)
        return "stdio-server"

    class Toolset:
        def __init__(self, **kwargs: object):
            observed["toolset"] = kwargs

        async def setup(self, *, reload: bool = False):
            return self

        def filter_tools(self, predicate):
            observed["filter"] = predicate
            return self

    fake_mcp = SimpleNamespace(MCPServerStdio=server, MCPToolset=Toolset)
    config_path = write_reachy_config(tmp_path)
    launcher = tmp_path / "forge-puppeteer-mcp"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-forward")
    monkeypatch.setenv("HERMES_API_KEY", "must-not-forward-either")
    opts = load_integrations({
        "PUPPETEER_MCP_CONFIG": str(config_path),
        "PUPPETEER_MCP_COMMAND": str(launcher),
        "PUPPETEER_MCP_ALLOWED_TOOLS": "play_move,list_assets,action_lookup,device_stop",
    })
    tools = build_assistant_tools(opts, mcp_module=fake_mcp)
    assert observed["command"] == str(launcher)
    assert observed["args"] == ["--config", str(config_path)]
    assert observed["env"] == {}
    assert observed["toolset"] == {"id": "approved-puppeteer", "mcp_server": "stdio-server"}
    assert len(tools) == 1

    for invalid_command in ("./forge-puppeteer-mcp", str(tmp_path / "wrong-name")):
        with pytest.raises(ValueError, match="PUPPETEER_MCP_COMMAND"):
            load_integrations({
                "PUPPETEER_MCP_CONFIG": str(config_path),
                "PUPPETEER_MCP_COMMAND": invalid_command,
                "PUPPETEER_MCP_ALLOWED_TOOLS": "play_move,list_assets,action_lookup,device_stop",
            })


@pytest.mark.anyio
async def test_puppeteer_stdio_fake_server_exposes_only_granted_tools(tmp_path: Path, monkeypatch) -> None:
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    executable = executable_dir / "forge-puppeteer-mcp"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "config_path = Path(sys.argv[2])\n"
        "(config_path.parent / 'child-env.json').write_text(json.dumps({\n"
        "  'openrouter': bool(os.getenv('OPENROUTER_API_KEY')),\n"
        "  'hermes': bool(os.getenv('HERMES_API_KEY')),\n"
        "}), encoding='utf-8')\n"
        "from mcp.server import MCPServer\n"
        "server = MCPServer('fake-puppeteer')\n"
        "@server.tool()\n"
        "async def play_move(action_id: str) -> str:\n"
        "    return action_id\n"
        "@server.tool()\n"
        "async def list_assets() -> str:\n"
        "    return 'only operator-approved moves'\n"
        "@server.tool()\n"
        "async def action_lookup(action_id: str) -> str:\n"
        "    return action_id\n"
        "@server.tool()\n"
        "async def device_stop() -> str:\n"
        "    return 'stopped'\n"
        "@server.tool()\n"
        "async def head_pose(action_id: str) -> str:\n"
        "    return action_id\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(executable_dir))
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-inherited-by-mcp-child")
    monkeypatch.setenv("HERMES_API_KEY", "not-inherited-by-mcp-child")

    from livekit.agents import mcp

    config_path = write_reachy_config(tmp_path)
    integrations = load_integrations({
        "PUPPETEER_MCP_CONFIG": str(config_path),
        "PUPPETEER_MCP_COMMAND": str(executable),
        "PUPPETEER_MCP_ALLOWED_TOOLS": "play_move,list_assets,action_lookup,device_stop",
    })
    [toolset] = build_assistant_tools(integrations, mcp_module=mcp)
    try:
        await toolset.setup()
        assert [tool._info.name for tool in toolset.tools] == [
            "play_move", "list_assets", "action_lookup", "device_stop"
        ]
        child_environment = json.loads((tmp_path / "child-env.json").read_text(encoding="utf-8"))
        assert child_environment == {"openrouter": False, "hermes": False}
    finally:
        await toolset.aclose()
