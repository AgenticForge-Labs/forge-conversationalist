"""Real-stdio and configuration tests for optional Puppeteer three-robot room.

Every child in this file is a synthetic MCP server: no SDK, USB, or robot
network connection is opened and no physical motion is authorized.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from forge_conversationalist.agent import _room_instructions
from forge_conversationalist.assistant_tools import build_assistant_tools
from forge_conversationalist.ensemble import EnsembleSession, PuppeteerEnsembleConfig
from forge_conversationalist.integrations import Integrations, load_integrations


def operator_file(tmp_path: Path, **overrides) -> Path:
    data = {
        "schema_version": 1,
        "motion_authority": "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION",
        "reachy": {"backend": "reachy_sdk", "device": "reachy", "instance_id": "reachy"},
        "stackchan": {
            "backend": "stackchan_sdk", "device": "stackchan", "instance_id": "stackchan"
        },
        "soarm101": {"instance_id": "soarm101", "mode": "simulation"},
        "ownership_db": str(tmp_path / "owner.db"),
        "action_journal": str(tmp_path / "action.db"),
        "reachy_neutral_head": str(tmp_path / "head.json"),
    }
    data.update(overrides)
    path = tmp_path / "ensemble.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_native_ensemble_disabled_by_default_and_no_new_agent_identity(tmp_path):
    assert load_integrations({}) == Integrations()
    configured = load_integrations({
        "PUPPETEER_ENSEMBLE_CONFIG": str(operator_file(tmp_path)),
    })
    assert configured.ensemble is not None
    assert configured.embodiment is None
    assert configured.puppeteer_stdio is None
    assert set(configured.ensemble.performer_ids) == {"reachy", "stackchan", "soarm101"}
    assert "4g" not in configured.ensemble.config_path.read_text().lower()
    assert "request_ensemble_gesture" in _room_instructions(
        configured, caller_id="operator", session_id="room"
    )


@pytest.mark.parametrize("changes", [
    {"motion_authority": None},
    {"schema_version": True},
    {"schema_version": 2},
    {"reachy": {"backend": "fake", "device": "reachy", "instance_id": "reachy"}},
    {"stackchan": {"backend": "fake", "device": "stackchan", "instance_id": "stackchan"}},
    {"soarm101": {"instance_id": "soarm101", "mode": "unknown"}},
    {"ownership_db": "relative.db"},
    {"reachy_neutral_head": "relative.json"},
    {"action_journal": "/tmp/shared.db", "ownership_db": "/tmp/shared.db"},
])
def test_operator_configuration_denies_missing_authority_or_native_identity(tmp_path, changes):
    path = operator_file(tmp_path, **changes)
    with pytest.raises((ValueError, TypeError)):
        load_integrations({"PUPPETEER_ENSEMBLE_CONFIG": str(path)})


def test_operator_file_digest_detects_changes(tmp_path):
    path = operator_file(tmp_path)
    config = PuppeteerEnsembleConfig(path)
    path.write_text(path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="changed"):
        config.require_unchanged()


def test_native_ensemble_is_exclusive_of_legacy_reachy_and_fake_puppeteer(tmp_path):
    path = operator_file(tmp_path)
    legacy = tmp_path / "embodiment.json"
    legacy.write_text(json.dumps({
        "backend": "fake", "device": "reachy", "instance_id": "reachy-fake",
        "allowed_tools": [
            "device_info", "performer_discover", "conversation_presence",
            "expression_request", "action_lookup",
        ],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="alongside"):
        load_integrations({
            "PUPPETEER_ENSEMBLE_CONFIG": str(path),
            "PUPPETEER_EMBODIMENT_CONFIG": str(legacy),
        })
    with pytest.raises(ValueError):
        load_integrations({
            "PUPPETEER_ENSEMBLE_CONFIG": str(path),
            "PUPPETEER_MCP_CONFIG": str(tmp_path / "missing-legacy.json"),
        })


def test_ensemble_agent_tools_only_status_and_exact_explicit_gestures(tmp_path):
    class Stub:
        def __init__(self):
            self.expressions = []

        async def status(self):
            return {"schema": "forge.behavior.v1", "state": "speaking"}

        async def express(self, name):
            self.expressions.append(name)
            return {"outcome": "accepted", "expression": name}

    registered = {}

    def factory(fn, *, name, description):
        registered[name] = fn
        assert description
        return fn

    cfg = load_integrations({
        "PUPPETEER_ENSEMBLE_CONFIG": str(operator_file(tmp_path)),
    })
    stub = Stub()
    tools = build_assistant_tools(
        cfg, ensemble_session=stub, function_tool_factory=factory,
    )
    assert len(tools) == 2
    assert set(registered) == {"get_ensemble_status", "request_ensemble_gesture"}

    async def run():
        assert json.loads(await registered["get_ensemble_status"]())["state"] == "speaking"
        assert json.loads(await registered["request_ensemble_gesture"]("yes")) == {
            "outcome": "accepted", "expression": "yes",
        }

    asyncio.run(run())
    assert stub.expressions == ["yes"]
    assert build_assistant_tools(cfg, function_tool_factory=factory) == []


@pytest.mark.anyio
async def test_native_ensemble_mcp_stdio_one_room_events_no_secret_forwarding(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    events = tmp_path / "events.jsonl"
    path = operator_file(tmp_path)
    script = tmp_path / "forge-puppeteer-ensemble"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "from typing import TypedDict\n"
        "from mcp.server import MCPServer\n"
        "class State(TypedDict):\n"
        "    schema: str\n"
        "    session_id: str | None\n"
        "    state: str\n"
        "    performers: dict[str, dict]\n"
        "class Outcome(TypedDict):\n"
        "    outcome: str\n"
        "    expression: str\n"
        "class Presence(TypedDict):\n"
        "    outcome: str\n"
        "    state: str\n"
        "app = MCPServer('offline-ensemble')\n"
        f"log = Path({str(events)!r})\n"
        "def write(kind, value):\n"
        "    with log.open('a') as f:\n"
        "        f.write(json.dumps({'kind': kind, 'value': value,\n"
        "            'llm_env': 'OPENROUTER_API_KEY' in os.environ}) + '\\n')\n"
        "@app.tool()\n"
        "async def ensemble_state() -> State:\n"
        "    return {'schema':'forge.behavior.v1','session_id':None,\n"
        "       'state':'disconnected','performers':{\n"
        "       'reachy':{},'stackchan':{},'soarm101':{}}}\n"
        "@app.tool()\n"
        "async def ensemble_presence(session_id: str, event_id: str,\n"
        "   sequence: int, state: str, source: str = 'agent_state',\n"
        "   interrupted: bool = False) -> Presence:\n"
        "    write('presence',locals())\n"
        "    return {'outcome':'accepted','state':state}\n"
        "@app.tool()\n"
        "async def ensemble_expression(expression: str, action_id: str) -> Outcome:\n"
        "    write('expression', locals())\n"
        "    return {'outcome':'accepted','expression':expression}\n"
        "app.run(transport='stdio')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-leak-to-robot")
    monkeypatch.setenv("HERMES_API_KEY", "must-not-leak-to-robot")
    config = PuppeteerEnsembleConfig(path, command=str(script))
    room = EnsembleSession(config, room_id="livekit-one-room", caller_id="verified-participant")
    try:
        status = await room.start()
        assert status["state"] == "disconnected"
        room.observe("listening")
        room.observe("speaking")
        await asyncio.sleep(0.03)
        outcome = await room.express("yes")
        assert outcome == {"outcome": "accepted", "expression": "yes"}
        assert (await room.express("raw_motor_goal"))["outcome"] == "unsupported"
        assert (await room.status())["state"] == "disconnected"
    finally:
        await room.close()
    logged = [json.loads(line) for line in events.read_text().splitlines()]
    assert [e["kind"] for e in logged] == [
        "presence", "presence", "expression", "presence",
    ]
    presence = [e["value"] for e in logged if e["kind"] == "presence"]
    assert [x["state"] for x in presence] == ["listening", "speaking", "disconnected"]
    assert [x["sequence"] for x in presence] == [0, 1, 2]
    assert all(x["source"] == "agent_state" for x in presence)
    assert all(not e["llm_env"] for e in logged)


@pytest.fixture
def anyio_backend():
    return "asyncio"
