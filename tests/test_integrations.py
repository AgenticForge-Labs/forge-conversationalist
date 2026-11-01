"""No real MCP server, Hermes gateway or robot is used by these tests."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

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
