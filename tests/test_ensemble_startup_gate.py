"""The startup question, its answer, and parking when a session ends.

Every child is a synthetic MCP server: no SDK, USB or robot connection is opened.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from v4_config import write_ensemble

from forge_conversationalist.ensemble import EnsembleSession, PuppeteerEnsembleConfig

pytest.importorskip("mcp")
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def child(tmp_path: Path, *, gate=True, park=True, park_result=None, ready=False):
    """A fake Puppeteer ensemble; returns (launcher path, events log path)."""
    events = tmp_path / "events.jsonl"
    park_result = park_result or {
        "outcome": "parked", "results": {"reachy": {"outcome": "at_sleep"},
                                         "stackchan": {"outcome": "already_at_sleep"}}}
    held = {"reachy": {"status": "not_at_sleep", "can_move_to_sleep": True,
                       "deltas_deg": {"shoulder_lift": 12.0},
                       "message": "reachy is not at sleep: shoulder_lift +12.0 deg."},
            "stackchan": {"status": "at_sleep", "message": "stackchan is at its sleep position."}}
    ok = {name: {"status": "at_sleep", "message": "ok"} for name in held}
    lines = [
        f"#!{sys.executable}",
        "import json, asyncio",
        "from pathlib import Path",
        "from mcp.server import MCPServer",
        "app = MCPServer('offline-ensemble')",
        f"log = Path({str(events)!r})",
        "def write(kind, value):",
        "    with log.open('a') as f:",
        "        f.write(json.dumps({'kind': kind, 'value': value}) + '\\n')",
        "IDLE = {'reachy': {'status': 'idle'}, 'stackchan': {'status': 'idle'}}",
        "@app.tool()",
        "async def ensemble_state() -> dict[str, object]:",
        "    return {'schema': 'forge.behavior.v1', 'session_id': None, 'state': 'disconnected',",
        "      'performers': {n: {'expressions': ['yes'], 'status': 'idle',",
        "        'behavior_revision': 'sha256:' + 'a' * 64,",
        "        'presence_behaviors': ['idle', 'listening', 'speaking']} for n in ('reachy', 'stackchan')}}",
        "@app.tool()",
        "async def ensemble_presence(session_id: str, event_id: str, sequence: int, state: str,",
        "    source: str = 'agent_state', interrupted: bool = False) -> dict[str, object]:",
        "    write('presence', {'state': state})",
        "    return {'outcome': 'accepted', 'state': state, 'performers': IDLE}",
        "@app.tool()",
        "async def ensemble_expression(expression: str, action_id: str,",
        "    performer_ids: list[str] | None = None) -> dict[str, object]:",
        "    return {'outcome': 'accepted', 'expression': expression}",
    ]
    if gate:
        lines += [
            "@app.tool()",
            "async def ensemble_startup_check() -> dict[str, object]:",
            "    write('startup_check', {})",
            f"    return {{'outcome': 'observed', 'ready': {ready!r}, 'performers': {(ok if ready else held)!r}}}",
            "@app.tool()",
            "async def ensemble_prepare(performer_id: str, action: str, action_id: str = '') -> dict[str, object]:",
            "    write('prepare', {'performer_id': performer_id, 'action': action, 'action_id': action_id})",
            "    await asyncio.sleep(0.05)",
            "    return {'outcome': 'at_sleep', 'ready': True}",
        ]
    if park:
        lines += [
            "@app.tool()",
            "async def ensemble_park(action_id: str) -> dict[str, object]:",
            "    write('park', {'action_id': action_id})",
            "    await asyncio.sleep(0.05)",
            f"    return {park_result!r}",
        ]
    lines.append("app.run(transport='stdio')")
    script = tmp_path / "forge-puppeteer-ensemble"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script, events


def session(tmp_path, script, **kwargs):
    path = write_ensemble(tmp_path, kinds=("reachy", "stackchan"))
    return EnsembleSession(PuppeteerEnsembleConfig(path, command=str(script)),
                           room_id="room", caller_id="verified", **kwargs)


def logged(events: Path):
    return [json.loads(line) for line in events.read_text().splitlines()]


async def test_the_startup_question_is_read_only_and_the_answer_is_one_exact_call(tmp_path):
    script, events = child(tmp_path)
    room = session(tmp_path, script)
    try:
        await room.start()
        question = await room.startup()
        assert question["ready"] is False
        assert question["performers"]["reachy"]["deltas_deg"] == {"shoulder_lift": 12.0}
        answer = await room.prepare("reachy", "move_to_sleep")
        assert answer["outcome"] == "at_sleep"
        # Anything outside the two known actions or the pinned robots never reaches the robots.
        assert (await room.prepare("reachy", "jog_joint"))["outcome"] == "unsupported"
        assert (await room.prepare("invented-robot", "move_to_sleep"))["outcome"] == "unsupported"
        assert (await room.prepare(7, "move_to_sleep"))["outcome"] == "unsupported"
    finally:
        await room.close()
    calls = [e for e in logged(events) if e["kind"] == "prepare"]
    assert len(calls) == 1
    assert calls[0]["value"]["performer_id"] == "reachy" and calls[0]["value"]["action"] == "move_to_sleep"
    assert calls[0]["value"]["action_id"].startswith("sleep-")


async def test_gate_tools_missing_on_an_older_puppeteer_are_reported_not_called(tmp_path):
    script, events = child(tmp_path, gate=False, park=False)
    room = session(tmp_path, script)
    try:
        await room.start()
        assert (await room.startup())["reason"] == "startup_gate_not_available"
        assert (await room.prepare("reachy", "move_to_sleep"))["reason"] == "startup_gate_not_available"
    finally:
        await room.close()
    assert not [e for e in logged(events) if e["kind"] in {"startup_check", "prepare", "park"}]


async def test_closing_a_session_parks_the_robots_after_the_disconnect_and_reports_it(tmp_path):
    script, events = child(tmp_path)
    room = session(tmp_path, script)
    await room.start()
    room.observe("listening")
    evidence = await room.close()
    kinds = [e["kind"] for e in logged(events)]
    assert kinds.index("park") > max(i for i, k in enumerate(kinds) if k == "presence")
    assert logged(events)[[i for i, k in enumerate(kinds) if k == "presence"][-1]]["value"]["state"] == "disconnected"
    assert evidence["outcome"] == "acknowledged"
    assert evidence["sleep_request"] == "parked"
    assert evidence["sleep_performers"] == {"reachy": "at_sleep", "stackchan": "already_at_sleep"}
    assert evidence["physical_sleep_verified"] is True


async def test_a_park_fault_is_reported_not_retried_and_does_not_hang_the_close(tmp_path):
    fault = {"outcome": "stopped_on_fault", "performer": "reachy",
             "results": {"reachy": {"outcome": "failed"}}}
    script, events = child(tmp_path, park_result=fault)
    room = session(tmp_path, script)
    await room.start()
    evidence = await room.close()
    assert [e["kind"] for e in logged(events)].count("park") == 1
    assert evidence["sleep_request"] == "stopped_on_fault"
    assert evidence["sleep_performers"] == {"reachy": "failed"}
    assert evidence["physical_sleep_verified"] is False


@pytest.mark.parametrize("kwargs,has_park_tool", [({"park_on_close": False}, True), ({}, False)])
async def test_no_park_when_disabled_or_when_puppeteer_has_no_such_tool(tmp_path, kwargs, has_park_tool):
    script, events = child(tmp_path, park=has_park_tool)
    room = session(tmp_path, script, **kwargs)
    await room.start()
    evidence = await room.close()
    assert "park" not in [e["kind"] for e in logged(events)]
    assert evidence["sleep_request"] == "not_attempted"
    assert evidence["physical_sleep_verified"] is False
