"""Fake-only config and optional Puppeteer MCP wire/session regression tests."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from forge_conversationalist.assistant_tools import build_assistant_tools
from forge_conversationalist.embodiment import EmbodimentSession
from forge_conversationalist.integrations import Integrations, load_integrations

GRANTS = [
    "device_info", "performer_discover", "conversation_presence",
    "expression_request", "action_lookup",
]


def profile(tmp_path: Path, device: str, **overrides) -> Path:
    path = tmp_path / f"{device}.json"
    data = {
        "backend": "fake", "device": device, "instance_id": f"{device}-sim",
        "allowed_tools": GRANTS,
        "assets": ["emotion:curious", "dance:hello", "gesture:wave"],
    }
    path.write_text(json.dumps(data | overrides), encoding="utf-8")
    return path


@pytest.mark.parametrize("device", ["reachy", "stackchan", "soarm101"])
def test_configured_fake_performer_and_robot_free_default(tmp_path, device):
    assert load_integrations({}) == Integrations()
    path = profile(tmp_path, device)
    conf = load_integrations({"PUPPETEER_EMBODIMENT_CONFIG": str(path)})
    assert conf.embodiment is not None
    assert conf.embodiment.performer_id == f"{device}-sim"
    assert conf.puppeteer_stdio is None
    assert "token" not in repr(conf.embodiment).lower()


@pytest.mark.parametrize("changes", [
    {"backend": "reachy_sdk"},
    {"backend": "stackchan_sdk"},
    {"backend": "soarm101_broker"},
    {"physical_authority": "I_AUTHORIZE_REACHY_CONTROL"},
    {"allowed_tools": ["device_info", "expression_request"]},
    {"allowed_tools": GRANTS + ["device_stop"]},
    {"instance_id": ""},
    {"device": "unknown"},
])
def test_fake_only_config_rejects_hardware_and_widened_tools(tmp_path, changes):
    path = profile(tmp_path, "reachy", **changes)
    with pytest.raises(ValueError):
        load_integrations({"PUPPETEER_EMBODIMENT_CONFIG": str(path)})


def test_embodiment_tool_wrapper_uses_same_room_client(tmp_path):
    class Stub:
        async def discover(self):
            return {"schema": "forge.embodiment.v1", "execution_mode": "fake"}

        async def express(self, kind, name, revision):
            return {"outcome": "accepted", "kind": kind, "name": name, "revision": revision}

        async def lookup(self, action_id):
            return {"id": action_id, "state": "queued"}

    tools = {}
    def factory(func, *, name, description):
        assert description
        tools[name] = func
        return func

    config = load_integrations({"PUPPETEER_EMBODIMENT_CONFIG": str(profile(tmp_path, "reachy"))})
    built = build_assistant_tools(
        config, function_tool_factory=factory, embodiment_session=Stub()
    )
    assert len(built) == 3
    assert set(tools) == {
        "discover_performer", "request_performer_expression", "check_performer_action"
    }

    async def run():
        assert json.loads(await tools["discover_performer"]())["execution_mode"] == "fake"
        assert json.loads(await tools["request_performer_expression"](
            "emotion", "curious", "sha256:fixture"
        ))["name"] == "curious"
        assert json.loads(await tools["check_performer_action"]("a1"))["state"] == "queued"
    asyncio.run(run)


@pytest.mark.anyio
@pytest.mark.parametrize("device", ["reachy", "stackchan", "soarm101"])
async def test_fake_performer_mcp_wire_and_disconnect(tmp_path, monkeypatch, device):
    pytest.importorskip("mcp")
    # An independent real MCP stdio server validates Conversationalist's
    # startup, call serialization, content-free observations and exit behavior.
    # Puppeteer's own PR #10 separately tests its real fake MCP implementation.
    server = tmp_path / "forge-puppeteer-mcp"
    events = tmp_path / "events.jsonl"
    server.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "from mcp.server import MCPServer\n"
        "config = json.loads(Path(sys.argv[2]).read_text())\n"
        f"log = Path({str(events)!r})\n"
        "app = MCPServer('fake-reference')\n"
        "def record(kind, data):\n"
        "    with log.open('a') as f: f.write(json.dumps({'kind': kind, 'data': data})+'\\n')\n"
        "@app.tool()\n"
        "async def performer_discover() -> dict:\n"
        "    return {'schema':'forge.embodiment.v1', 'execution_mode':'fake',\n"
        "            'performer_id':config['instance_id'],\n"
        "            'physical_qualification':'not_qualified',\n"
        "            'expressions':[{'kind':'emotion','name':'curious',\n"
        "            'revision':'fixture'}]}\n"
        "@app.tool()\n"
        "async def conversation_presence(event: dict) -> dict:\n"
        "    record('presence',event)\n"
        "    return {'outcome':'accepted'}\n"
        "@app.tool()\n"
        "async def expression_request(request: dict) -> dict:\n"
        "    record('expression',request)\n"
        "    return {'outcome':'accepted','action_id':request['action_id'],\n"
        "            'execution_mode':'fake'}\n"
        "@app.tool()\n"
        "async def action_lookup(action_id: str) -> dict:\n"
        "    return {'id':action_id,'state':'completed',\n"
        "            'completion_evidence':'fake-only'}\n"
        "app.run(transport='stdio')\n",
        encoding="utf-8",
    )
    server.chmod(0o755)
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-be-forwarded")
    monkeypatch.setenv("HERMES_API_KEY", "must-not-be-forwarded")
    conf = load_integrations({
        "PUPPETEER_EMBODIMENT_CONFIG": str(profile(tmp_path, device)),
        "PUPPETEER_EMBODIMENT_COMMAND": str(server),
    }).embodiment
    assert conf is not None
    bridge = EmbodimentSession(conf, room_id="room-test", caller_id="authorized-user")
    try:
        catalog = await bridge.start()
        assert catalog["performer_id"] == f"{device}-sim"
        bridge.observe("listening")
        bridge.observe("speaking")
        bridge.observe("idle")
        result = await bridge.express("emotion", "curious", "fixture")
        assert result["outcome"] == "accepted"
        status = await bridge.lookup(result["action_id"])
        assert status["state"] == "completed"
    finally:
        await bridge.close()
    logged = [json.loads(line) for line in events.read_text().splitlines()]
    present = [e["data"] for e in logged if e["kind"] == "presence"]
    assert [p["state"] for p in present] == [
        "listening", "speaking", "idle", "disconnected",
    ]
    assert [p["sequence"] for p in present] == [0, 1, 2, 3]
    assert all(p["source"] == "agent_state" for p in present)
    assert logged[-2]["kind"] == "expression"
    assert logged[-2]["data"]["caller_id"] == "authorized-user"
