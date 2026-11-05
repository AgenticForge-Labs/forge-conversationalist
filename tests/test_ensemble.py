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
from forge_conversationalist.ensemble import (
    SPEECH_ENVELOPE_MAX_QUEUE,
    EnsembleSession,
    PuppeteerEnsembleConfig,
)
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


def test_ensemble_presence_source_is_explicitly_validated(tmp_path):
    client = EnsembleSession(
        PuppeteerEnsembleConfig(operator_file(tmp_path)),
        room_id="room", caller_id="operator",
    )
    assert client._presence("speaking", source="playback")["source"] == "playback"
    with pytest.raises(ValueError, match="timing source"):
        client.observe("speaking", source="device-clock")


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


def test_reachy_only_ensemble_requires_explicit_schema(tmp_path):
    path = operator_file(tmp_path)
    data = json.loads(path.read_text())
    data.update(schema_version=2)
    del data["stackchan"]
    del data["soarm101"]
    path.write_text(json.dumps(data))
    assert PuppeteerEnsembleConfig(path).performer_ids == ("reachy",)
    data["soarm101"] = {}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        PuppeteerEnsembleConfig(path)


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

        async def express(self, name, performer_id):
            self.expressions.append((name, performer_id))
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
        result = json.loads(await registered["request_ensemble_gesture"]("yes", "reachy"))
        assert result["outcome"] == "accepted" and result["expression"] == "yes"
        assert result["completion"] == "unconfirmed"
        assert "not confirmed complete" in result["spoken_guidance"]

    asyncio.run(run())
    assert stub.expressions == [("yes", "reachy")]
    assert build_assistant_tools(cfg, function_tool_factory=factory) == []


@pytest.mark.anyio
async def test_native_ensemble_mcp_stdio_one_room_events_no_secret_forwarding(
    tmp_path, monkeypatch, caplog,
):
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
        "       'reachy':{'expressions':['yes','no','happy'],'status':'idle',\n"
        "          'behavior_revision':'sha256:'+'a'*64,\n"
        "          'presence_behaviors':['idle','listening','speaking']},\n"
        "       'stackchan':{'expressions':['yes'],'status':'idle',\n"
        "          'behavior_revision':'sha256:'+'b'*64,\n"
        "          'presence_behaviors':['idle','listening','speaking']},\n"
        "       'soarm101':{'expressions':['yes','no'],'status':'idle',\n"
        "          'behavior_revision':'sha256:'+'c'*64,\n"
        "          'presence_behaviors':['idle','listening','speaking']}}}\n"
        "@app.tool()\n"
        "async def ensemble_presence(session_id: str, event_id: str,\n"
        "   sequence: int, state: str, source: str = 'agent_state',\n"
        "   interrupted: bool = False) -> Presence:\n"
        "    write('presence',locals())\n"
        "    return {'outcome':'accepted','state':state}\n"
        "@app.tool()\n"
        "async def ensemble_expression(expression: str, action_id: str,\n"
        "   performer_ids: list[str] | None = None) -> Outcome:\n"
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
        with caplog.at_level("INFO", logger="forge_conversationalist.ensemble"):
            room.observe("listening")
            room.observe("speaking", source="playback")
            room.observe("listening", source="playback", interrupted=True)
            await asyncio.sleep(0.03)
            outcome = await room.express("yes", "reachy")
            assert outcome == {"outcome": "accepted", "expression": "yes"}
            assert (await room.express("raw_motor_goal", "reachy"))["outcome"] == "unsupported"
            assert (await room.express("happy", "stackchan"))["outcome"] == "unsupported"
            assert (await room.express("yes", "invented-robot"))["outcome"] == "unsupported"
            assert (await room.status())["state"] == "disconnected"
    finally:
        await room.close()
    logged = [json.loads(line) for line in events.read_text().splitlines()]
    # Rapid intermediate presence may be coalesced by urgent interruption:
    # no queued speaking cue may jump ahead of the latest barge-in.
    assert logged[-1]["kind"] == "presence"
    assert logged[-1]["value"]["state"] == "disconnected"
    assert "expression" in [e["kind"] for e in logged]
    assert [e["value"]["performer_ids"] for e in logged if e["kind"] == "expression"] == [["reachy"]]
    presence = [e["value"] for e in logged if e["kind"] == "presence"]
    assert presence[-1]["state"] == "disconnected"
    assert any(x["interrupted"] for x in presence[:-1])
    assert [x["sequence"] for x in presence] == sorted(x["sequence"] for x in presence)
    interrupted_index = next(i for i, x in enumerate(presence) if x["interrupted"])
    assert all(x["state"] != "speaking" for x in presence[interrupted_index:])
    assert all(x["source"] in {"agent_state", "playback"} for x in presence)
    assert all(not e["llm_env"] for e in logged)
    enqueued = [r for r in caplog.records if r.msg == "puppeteer_presence_enqueued"]
    calls = [r for r in caplog.records if r.msg == "puppeteer_presence_call_started"]
    acks = [r for r in caplog.records if r.msg == "puppeteer_presence_call_completed"]
    assert [r.sequence for r in enqueued] == [0, 1, 2]
    assert [r.sequence for r in calls] == sorted(r.sequence for r in calls)
    assert [r.sequence for r in acks] == [r.sequence for r in calls]
    assert all(r.session_id == "livekit-one-room" for r in enqueued + calls + acks)
    assert all(r.mcp_outcome == "accepted" for r in acks)
    assert all(r.call_started_at_monotonic_ns > 0 for r in calls)
    assert all(r.acknowledged_at_monotonic_ns > 0 for r in acks)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_optional_outgoing_speech_features_use_existing_room_queue(tmp_path):
    class RunningTask:
        def done(self):
            return False

    room = EnsembleSession(
        PuppeteerEnsembleConfig(operator_file(tmp_path)),
        room_id="room-1", caller_id="authorized",
        outgoing_speech_envelope=True,
    )
    room._task = RunningTask()
    room._speech_capable = True
    packet = {
        "schema": "forge.speech_envelope.v1",
        "stream_id": "tts-1", "sequence": 0, "offset_ms": 0,
        "duration_ms": 50, "active": True, "intensity": 0.5,
        "rms_dbfs": -20.0, "timing_source": "generated_pcm",
        "acoustic_playout_measured": False,
    }
    assert room.offer_speech_envelope([packet]) is True
    tool, args, future = room._queue.get_nowait()
    assert tool == "ensemble_speech_envelope"
    # The batch carries the performer that owns the stream so the child can
    # gate it; an unattributed stream shares one explicit slot.
    assert args == {
        "session_id": "room-1", "performer_id": "unattributed", "frames": [packet],
    }
    assert future is None
    assert room._closed is False


def test_optional_audio_queue_backpressure_never_disables_conversation(tmp_path):
    class RunningTask:
        def done(self):
            return False

    room = EnsembleSession(
        PuppeteerEnsembleConfig(operator_file(tmp_path)),
        room_id="room-1", caller_id="authorized",
        outgoing_speech_envelope=True,
    )
    room._task = RunningTask()
    room._speech_capable = True
    for index in range(SPEECH_ENVELOPE_MAX_QUEUE):
        assert room.offer_speech_envelope([{
            "stream_id": "tts-1", "sequence": index,
        }])
    assert room.offer_speech_envelope([{
        "stream_id": "tts-1", "sequence": SPEECH_ENVELOPE_MAX_QUEUE,
    }]) is False
    assert room._closed is False
    assert room._queue.qsize() == SPEECH_ENVELOPE_MAX_QUEUE
    assert room.offer_speech_envelope([{
        "stream_id": "tts-1", "sequence": SPEECH_ENVELOPE_MAX_QUEUE + 1,
    }]) is False
    # Even when the stream is dropped, urgent presence retains space in
    # the same queue. Optional features never create a parallel MCP child.
    room.observe("listening", interrupted=True)
    assert room._closed is False
    assert room._queue.qsize() == 1  # optional batches discarded; urgent STOP first
    assert room._queue.get_nowait()[0] == "ensemble_presence"


def test_audio_ingress_is_inert_without_capability_and_explicit_opt_in(tmp_path):
    class RunningTask:
        def done(self):
            return False

    config = PuppeteerEnsembleConfig(operator_file(tmp_path))
    disabled = EnsembleSession(config, room_id="r", caller_id="c")
    disabled._task = RunningTask()
    disabled._speech_capable = True
    assert disabled.offer_speech_envelope([{"stream_id": "one"}]) is False
    enabled = EnsembleSession(
        config, room_id="r", caller_id="c",
        outgoing_speech_envelope=True,
    )
    enabled._task = RunningTask()
    assert enabled.offer_speech_envelope([{"stream_id": "one"}]) is False



def duo_operator_file(tmp_path: Path) -> Path:
    path = operator_file(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 3
    payload.pop("soarm101")
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_duo_uses_exact_two_robot_catalog_without_dummy_soarm(tmp_path):
    path = duo_operator_file(tmp_path)
    selected = PuppeteerEnsembleConfig(path)
    assert selected.performer_ids == ("reachy", "stackchan")
    configured = load_integrations({"PUPPETEER_ENSEMBLE_CONFIG": str(path)})
    assert configured.ensemble is not None
    assert configured.ensemble.performer_ids == ("reachy", "stackchan")
    for field in (None, {}, {"instance_id": "soarm101", "mode": "simulation"}):
        data = json.loads(path.read_text())
        data["soarm101"] = field
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="SO-ARM101"):
            PuppeteerEnsembleConfig(path)
    data.pop("soarm101")
    data["stackchan"]["instance_id"] = "reachy"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unique performer"):
        PuppeteerEnsembleConfig(path)


@pytest.mark.anyio
@pytest.mark.parametrize("extra_unconfigured_performer", [False, True])
async def test_duo_real_stdio_child_requires_exact_catalog(
    tmp_path, extra_unconfigured_performer,
):
    pytest.importorskip("mcp")
    config = duo_operator_file(tmp_path)
    script = tmp_path / "forge-puppeteer-ensemble"
    expected = {
        "reachy": {"expressions": ["yes"], "status": "idle",
                  "behavior_revision": "sha256:" + "a" * 64,
                  "presence_behaviors": ["idle", "listening", "speaking"]},
        "stackchan": {"expressions": ["yes", "no"], "status": "idle",
                     "behavior_revision": "sha256:" + "b" * 64,
                     "presence_behaviors": ["idle", "listening", "speaking"]},
    }
    if extra_unconfigured_performer:
        expected["soarm101"] = {
            "expressions": ["yes"], "status": "idle",
            "behavior_revision": "sha256:" + "c" * 64,
            "presence_behaviors": ["idle", "listening", "speaking"],
        }
    # The child only emulates the documented MCP contract. No SDK import,
    # hardware port, connection or actual robot action is allowed.
    script.write_text(
        f"#!{sys.executable}\n"
        "from mcp.server import MCPServer\n"
        "from typing import TypedDict\n"
        "class State(TypedDict):\n"
        "    schema: str\n"
        "    session_id: str | None\n"
        "    state: str\n"
        "    performers: dict[str, dict]\n"
        "class Presence(TypedDict):\n"
        "    outcome: str\n"
        "    state: str\n"
        "class Outcome(TypedDict):\n"
        "    outcome: str\n"
        "    expression: str\n"
        f"PERFORMERS = {expected!r}\n"
        "app = MCPServer('offline-duo-room')\n"
        "@app.tool()\n"
        "async def ensemble_state() -> State:\n"
        "    return {'schema': 'forge.behavior.v1', 'session_id': None,\n"
        "            'state': 'disconnected', 'performers': PERFORMERS}\n"
        "@app.tool()\n"
        "async def ensemble_presence(session_id: str, event_id: str,\n"
        "    sequence: int, state: str, source: str = 'agent_state',\n"
        "    interrupted: bool = False) -> Presence:\n"
        "    return {'outcome': 'accepted', 'state': state}\n"
        "@app.tool()\n"
        "async def ensemble_expression(expression: str, action_id: str,\n"
        "    performer_ids: list[str] | None = None) -> Outcome:\n"
        "    return {'outcome': 'accepted', 'expression': expression}\n"
        "app.run(transport='stdio')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    room = EnsembleSession(
        PuppeteerEnsembleConfig(config, command=str(script)),
        room_id="reviewed-livekit-room", caller_id="verified-caller",
    )
    try:
        if extra_unconfigured_performer:
            with pytest.raises(RuntimeError, match="verify_contract"):
                await room.start()
        else:
            state = await room.start()
            assert set(state["performers"]) == {"reachy", "stackchan"}
            assert (await room.express("yes", "reachy"))["outcome"] == "accepted"
            assert (await room.express("yes", "soarm101"))["outcome"] == "unsupported"
            room.observe("speaking")
            assert (await room.status())["state"] == "disconnected"
    finally:
        await room.close()


class _RunningTask:
    """Stands in for the live MCP serve task in queue-level tests."""

    def done(self):
        return False


def _duo_room(tmp_path, **kwargs):
    room = EnsembleSession(
        PuppeteerEnsembleConfig(operator_file(tmp_path)),
        room_id="room-duo", caller_id="authorized",
        outgoing_speech_envelope=True,
    )
    room._task = _RunningTask()
    room._speech_capable = True
    return room


def _hop(stream: str, sequence: int) -> dict:
    return {
        "schema": "forge.speech_envelope.v1", "stream_id": stream,
        "sequence": sequence, "offset_ms": sequence * 50, "duration_ms": 50,
        "active": True, "intensity": 0.5, "rms_dbfs": -20.0,
        "timing_source": "generated_pcm", "acoustic_playout_milliseconds": 0,
        "acoustic_playout_measured": False,
    }


def test_envelope_batches_are_attributed_per_performer(tmp_path):
    """Reachy (50 ms) and StackChan (2 s) streams stay distinguishable."""
    room = _duo_room(tmp_path)
    assert room.offer_speech_envelope([_hop("tts-a", 0)], performer_id="reachy")
    assert room.offer_speech_envelope([_hop("tts-b", 0)], performer_id="stackchan")
    queued = [room._queue.get_nowait() for _ in range(2)]
    assert [args["performer_id"] for _, args, _ in queued] == ["reachy", "stackchan"]
    # The shared runtime is serialized by construction: one queue, ordered.
    assert all(tool == "ensemble_speech_envelope" for tool, _, _ in queued)


def test_one_performer_backpressure_does_not_drop_the_other(tmp_path):
    """A starved fast stream must not silence the slow performer."""
    room = _duo_room(tmp_path)
    for index in range(SPEECH_ENVELOPE_MAX_QUEUE):
        assert room.offer_speech_envelope([_hop("tts-a", index)], performer_id="reachy")
    # Reachy's next batch is dropped and Reachy alone is fenced.
    assert room.offer_speech_envelope(
        [_hop("tts-a", SPEECH_ENVELOPE_MAX_QUEUE)], performer_id="reachy",
    ) is False
    assert "reachy" in room._speech_dropped_performers
    # StackChan still has its allocation.
    assert room.offer_speech_envelope([_hop("tts-b", 0)], performer_id="stackchan")
    assert "stackchan" not in room._speech_dropped_performers


def test_dropped_performer_is_never_silently_repaired(tmp_path):
    room = _duo_room(tmp_path)
    for index in range(SPEECH_ENVELOPE_MAX_QUEUE):
        room.offer_speech_envelope([_hop("tts-a", index)], performer_id="reachy")
    assert room.offer_speech_envelope([_hop("tts-a", 99)], performer_id="reachy") is False
    # Draining the queue must not re-arm a dropped stream.
    while not room._queue.empty():
        room._queue.get_nowait()
    assert room.offer_speech_envelope([_hop("tts-a", 100)], performer_id="reachy") is False


def test_unknown_performer_stream_is_refused(tmp_path):
    room = _duo_room(tmp_path)
    assert room.offer_speech_envelope([_hop("tts-x", 0)], performer_id="so101") is False
    assert room._queue.empty()


def test_urgent_presence_keeps_its_slot_behind_envelope_batches(tmp_path):
    """Envelope work can never delay a STOP."""
    room = _duo_room(tmp_path)
    for index in range(SPEECH_ENVELOPE_MAX_QUEUE):
        room.offer_speech_envelope([_hop("tts-a", index)], performer_id="reachy")
    room.observe("listening", interrupted=True)
    tools = []
    while not room._queue.empty():
        tools.append(room._queue.get_nowait()[0])
    assert "ensemble_presence" in tools
    assert room._queue.qsize() == 0
    assert room._closed is False
