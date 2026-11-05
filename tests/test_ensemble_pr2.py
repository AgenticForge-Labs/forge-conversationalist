"""PR2 deterministic no-motion lifecycle, priority and operator review tests."""

from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from forge_conversationalist.ensemble import (
    EnsembleSession,
    PuppeteerEnsembleConfig,
)
from forge_conversationalist.ensemble_preflight import main as preflight_main
from forge_conversationalist.playback_presence import PlaybackPresence
from forge_conversationalist.room_lifecycle import install_ensemble_room_shutdown_hooks
from forge_conversationalist.text_hook import ConversationStateChanged

REVISION = "sha256:" + "a" * 64


def operator_config(tmp_path: Path, *, duo=True, **extra) -> Path:
    payload = {
        "schema_version": 3 if duo else 2,
        "motion_authority": "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION",
        "reachy": {
            "backend": "reachy_sdk", "device": "reachy",
            "instance_id": "reachy", "allowed_tools": [
                "head_pose", "pose_and_antennas", "play_move", "device_stop",
            ],
            "physical_authority": "I_AUTHORIZE_REACHY_CONTROL",
        },
        "reachy_neutral_antennas_rad": [-0.17, 0.17],
        "reachy_neutral_head": str(tmp_path / "operator-neutral.json"),
        "action_journal": str(tmp_path / "actions.sqlite3"),
        "ownership_db": str(tmp_path / "owners.sqlite3"),
        "speech_driven_performers": ["reachy", "stackchan"] if duo else ["reachy"],
        "motion_profiles": {
            "reachy": {"idle": "smooth_v2", "speaking": "smooth_v2"},
        },
    }
    if duo:
        payload["stackchan"] = {
            "backend": "stackchan_sdk", "device": "stackchan",
            "instance_id": "stackchan",
            "allowed_tools": ["head_move", "face_update", "device_stop"],
            "physical_authority": "I_AUTHORIZE_STACKCHAN_CONTROL",
        }
        payload["motion_profiles"]["stackchan"] = {
            "idle": "native_v1", "speaking": "smooth_v2",
        }
    payload.update(extra)
    (tmp_path / "operator-neutral.json").write_text(json.dumps([
        [1, 0, 0, 0], [0, 1, 0, 0],
        [0, 0, 1, 0], [0, 0, 0, 1],
    ]))
    path = tmp_path / "approved.json"
    path.write_text(json.dumps(payload))
    return path


def test_static_preflight_is_nonmoving_and_does_not_open_devices(tmp_path, capsys):
    path = operator_config(tmp_path)
    config = PuppeteerEnsembleConfig(path)
    report = config.preflight()
    assert report["outcome"] == "static_config_valid"
    assert report["performer_ids"] == ["reachy", "stackchan"]
    assert report["motion_profiles"]["reachy"] == {
        "idle": "smooth_v2", "speaking": "smooth_v2",
    }
    assert all(report[key] is False for key in (
        "sdk_connections_opened", "physical_motion_dispatched",
        "physical_neutral_verified", "physical_stop_verified",
        "physical_sleep_verified", "acoustic_playout_measured",
    ))
    assert preflight_main(["--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["config_sha256"] == config.digest
    path.write_text(path.read_text() + " ")
    with pytest.raises(PermissionError):
        config.preflight()


@pytest.mark.parametrize("patch", [
    {"motion_profiles": {"unknown": {"idle": "native_v1"}}},
    {"motion_profiles": {"reachy": {"speaking": "magic"}}},
    {"motion_profiles": {"reachy": {"idle": "native_v1", "raw_servo": True}}},
    {"motion_profiles": {"reachy": {"speaking": "smooth_v2"}},
     "speech_driven_performers": []},
    {"motion_profiles": {"reachy": {"idle": "native_v1"}},
     "reachy_neutral_antennas_rad": None},
    {"speech_driven_performers": ["reachy", "reachy"]},
    {"speech_driven_performers": ["soarm101"]},
    {"motion_profiles": {"reachy": ["native_v1"]}},
])
def test_static_profile_preflight_denies_unsafe_changes(tmp_path, patch):
    path = operator_config(tmp_path, **patch)
    with pytest.raises((ValueError, TypeError)):
        PuppeteerEnsembleConfig(path)


def test_startup_contract_denies_wrong_catalog_and_blocked_robot(tmp_path):
    room = EnsembleSession(
        PuppeteerEnsembleConfig(operator_config(tmp_path)),
        room_id="signed-room", caller_id="owner",
    )
    performer = {
        "status": "idle", "behavior_revision": REVISION,
        "expressions": ["yes", "no"],
        "presence_behaviors": ["idle", "listening", "speaking"],
    }
    valid = {
        "schema": "forge.behavior.v1", "session_id": None,
        "state": "disconnected",
        "performers": {"reachy": dict(performer), "stackchan": dict(performer)},
    }
    room._verify_startup_state(valid)
    for altered in (
        {**valid, "performers": {"reachy": dict(performer)}},
        {**valid, "performers": {**valid["performers"], "reachy": {
            **performer, "behavior_revision": "invalid",
        }}},
        {**valid, "performers": {**valid["performers"], "stackchan": {
            **performer, "status": "blocked",
        }}},
        {**valid, "session_id": "foreign-room"},
    ):
        with pytest.raises(RuntimeError):
            room._verify_startup_state(altered)


class Active:
    def done(self):
        return False


def hop(stream, n):
    return {"schema": "forge.speech_envelope.v1", "stream_id": stream,
            "sequence": n, "offset_ms": 50*n, "duration_ms": 50,
            "intensity": 0.7, "active": True, "rms_dbfs": -18.0,
            "timing_source": "generated_pcm", "acoustic_playout_measured": False}


def test_barge_in_preempts_queued_pcm_and_never_rearms_old_utterance(tmp_path):
    room = EnsembleSession(
        PuppeteerEnsembleConfig(operator_config(tmp_path)),
        room_id="signed-room", caller_id="owner",
        outgoing_speech_envelope=True,
    )
    room._task = Active()
    room._speech_capable = True
    room.observe("speaking")
    for i in range(8):
        assert room.offer_speech_envelope([hop("old", i)], performer_id="reachy")
        assert room.offer_speech_envelope([hop("old", i)], performer_id="stackchan")
    room.observe("listening", interrupted=True)
    queued = []
    while not room._queue.empty():
        queued.append(room._queue.get_nowait())
    assert len(queued) == 1
    assert queued[0][0] == "ensemble_presence"
    assert queued[0][1]["interrupted"] is True
    assert not room.offer_speech_envelope([hop("old", 8)], performer_id="reachy")
    assert room.offer_speech_envelope([hop("new", 0)], performer_id="reachy")


@pytest.mark.anyio
@pytest.mark.parametrize("blocked", [False, True])
async def test_shutdown_reports_only_software_evidence_and_blocks_unknown_rearm(
    tmp_path, blocked,
):
    room = EnsembleSession(
        PuppeteerEnsembleConfig(operator_config(tmp_path)),
        room_id="room", caller_id="owner",
    )
    calls = []

    async def fake_server():
        while True:
            tool, args, future = await room._queue.get()
            calls.append((tool, args))
            if tool == "_close":
                future.set_result({
                    "outcome": "accepted",
                    "performers": {
                        "reachy": {"status": "blocked" if blocked else "idle"},
                        "stackchan": {"status": "idle"},
                    },
                })
                return
            if future is not None:
                future.set_result({"outcome": "accepted"})

    room._task = asyncio.create_task(fake_server())
    room.observe("speaking")
    outcome = await room.close()
    assert calls[-1][0] == "_close"
    assert outcome["outcome"] == ("unknown" if blocked else "acknowledged")
    assert outcome["neutral_request"] == "not_attempted"
    assert outcome["sleep_request"] == "not_attempted"
    assert outcome["physical_settling"] == "unconfirmed"
    assert outcome["physical_sleep_verified"] is False
    assert outcome["retry_safe"] is False
    assert await room.close() == outcome
    if blocked:
        with pytest.raises(RuntimeError, match="rearm"):
            await room.reconnect()


@pytest.mark.anyio
async def test_transport_lost_before_close_requires_operator_review(tmp_path):
    room = EnsembleSession(
        PuppeteerEnsembleConfig(operator_config(tmp_path)),
        room_id="room", caller_id="owner",
    )
    room._task = asyncio.create_task(asyncio.sleep(0))
    await room._task
    result = await room.close()
    assert result["outcome"] == "unknown"
    assert result["physical_settling"] == "unconfirmed"
    with pytest.raises(RuntimeError, match="rearm"):
        await room.reconnect()


@pytest.mark.anyio
async def test_authorized_owner_departure_requests_only_one_bounded_close():
    events = {}
    fake = SimpleNamespace(
        observe=Mock(), close=AsyncMock(return_value={"outcome": "acknowledged"}),
    )
    room = SimpleNamespace(on=lambda name, callback: events.__setitem__(name, callback))
    assert install_ensemble_room_shutdown_hooks(
        room, fake, authorized_identity="owner",
    ) is True
    events["participant_disconnected"](SimpleNamespace(identity="guest"))
    await asyncio.sleep(0)
    fake.close.assert_not_awaited()
    events["participant_disconnected"](SimpleNamespace(identity="owner"))
    events["disconnected"]()
    await asyncio.sleep(0.02)
    fake.close.assert_awaited_once()
    fake.observe.assert_called_once_with("listening", interrupted=True)


def test_playback_to_room_presence_preserves_interruption_provenance(tmp_path):
    room = EnsembleSession(
        PuppeteerEnsembleConfig(operator_config(tmp_path)),
        room_id="room", caller_id="owner",
    )
    room._task = Active()
    signals = {}
    output = SimpleNamespace(
        on=lambda name, listener: signals.__setitem__(name, listener),
        off=lambda name, listener: signals.pop(name, None),
    )
    adapter = PlaybackPresence(
        lambda state, source, interrupted: room.observe(
            state, source=source, interrupted=interrupted,
        ),
        session_id="room",
    )
    assert adapter.attach(output)
    adapter.observe(ConversationStateChanged(
        state="speaking", source="agent", interrupted=False,
    ))
    signals["playback_started"](SimpleNamespace(created_at=5))
    adapter.observe(ConversationStateChanged(
        state="listening", source="user", interrupted=True,
    ))
    signals["playback_started"](SimpleNamespace(created_at=6))
    queued = []
    while not room._queue.empty():
        queued.append(room._queue.get_nowait()[1])
    assert queued[-1]["state"] == "listening"
    assert queued[-1]["interrupted"] is True
    assert not any(x["state"] == "speaking" for x in queued)
    adapter.detach()



@pytest.mark.anyio
async def test_repeated_two_performer_real_stdio_fake_sessions_with_randomized_timing(
    tmp_path, monkeypatch,
):
    """Run the real MCP transport with a fake SDK-free server three times.

    Presence, optional PCM feature observation, gestures and disconnection
    share one room child; repeated sessions never use a previous SDK owner.
    No local serial, Pollen daemon, microphone or hardware process is opened.
    """
    pytest.importorskip("mcp")
    path = operator_config(tmp_path)
    log = tmp_path / "observed.jsonl"
    server = tmp_path / "forge-puppeteer-ensemble"
    server.write_text(
        f"#!{sys.executable}\n"
        "import asyncio, json, os\n"
        "from pathlib import Path\n"
        "from typing import TypedDict\n"
        "from mcp.server import MCPServer\n"
        "class State(TypedDict):\n"
        "    schema: str\n"
        "    session_id: str | None\n"
        "    state: str\n"
        "    performers: dict[str, dict]\n"
        "class Receipt(TypedDict):\n"
        "    outcome: str\n"
        "    state: str\n"
        "    performers: dict[str, dict]\n"
        "class EnvelopeReceipt(TypedDict):\n"
        "    outcome: str\n"
        "    frames_accepted: int\n"
        "app = MCPServer('synthetic-pr2-room')\n"
        f"log = Path({str(log)!r})\n"
        "performers = {\n"
        "  'reachy': {'status':'idle','behavior_revision':'sha256:'+'a'*64,\n"
        "             'expressions':['yes','no'],'presence_behaviors':['idle','listening','speaking']},\n"
        "  'stackchan': {'status':'idle','behavior_revision':'sha256:'+'b'*64,\n"
        "                'expressions':['yes','no'],'presence_behaviors':['idle','listening','speaking']},\n"
        "}\n"
        "def record(kind, **fields):\n"
        "    with log.open('a') as f:\n"
        "        f.write(json.dumps({'kind':kind, **fields,\n"
        "         'leaked_key': 'OPENROUTER_API_KEY' in os.environ})+'\\n')\n"
        "@app.tool()\n"
        "async def ensemble_state() -> State:\n"
        "    return {'schema':'forge.behavior.v1','session_id':None,\n"
        "            'state':'disconnected','performers':performers}\n"
        "@app.tool()\n"
        "async def ensemble_presence(session_id: str, event_id: str, sequence: int,\n"
        "    state: str, source: str='agent_state', interrupted: bool=False) -> Receipt:\n"
        "    await asyncio.sleep(0.002*(sequence%4))\n"
        "    record('presence', session=session_id, sequence=sequence,\n"
        "           state=state, source=source, interrupted=interrupted)\n"
        "    return {'outcome':'accepted','state':state,'performers':performers}\n"
        "@app.tool()\n"
        "async def ensemble_expression(expression: str, action_id: str,\n"
        "    performer_ids: list[str] | None=None) -> Receipt:\n"
        "    record('gesture', expression=expression, performers=performer_ids)\n"
        "    return {'outcome':'accepted','state':'speaking','performers':performers}\n"
        "@app.tool()\n"
        "async def ensemble_speech_envelope(session_id: str, frames: list[dict[str, object]],\n"
        "    performer_id: str | None=None) -> EnvelopeReceipt:\n"
        "    record('pcm', performer=performer_id, count=len(frames), session=session_id)\n"
        "    return {'outcome':'observed','frames_accepted':len(frames)}\n"
        "app.run(transport='stdio')\n",
        encoding="utf-8",
    )
    server.chmod(0o755)
    monkeypatch.setenv("OPENROUTER_API_KEY", "never-forward")
    rng = random.Random(113)
    for index in range(3):
        room = EnsembleSession(
            PuppeteerEnsembleConfig(path, command=str(server)),
            room_id=f"signed-room-{index}", caller_id="verified-owner",
            outgoing_speech_envelope=True,
        )
        state = await room.start()
        assert set(state["performers"]) == {"reachy", "stackchan"}
        assert room._speech_capable
        try:
            room.observe("listening")
            room.observe("speaking", source="playback")
            for sequence in range(3):
                batch = [hop(f"utterance-{index}", sequence)]
                assert room.offer_speech_envelope(
                    batch, performer_id="reachy",
                )
                assert room.offer_speech_envelope(
                    batch, performer_id="stackchan",
                )
                await asyncio.sleep(rng.uniform(0.005, 0.012))
            assert (await room.express("yes", "reachy"))["outcome"] == "accepted"
            # Request-state call drains all earlier optional PCM observations,
            # unless a later urgent interrupt superseded them.
            await room.status()
            room.observe("listening", interrupted=True)
            room.observe("idle")
            await room.status()
        finally:
            end = await room.close()
        assert end["outcome"] == "acknowledged"
        assert end["neutral_request"] == "not_attempted"
        assert end["physical_settling"] == "unconfirmed"
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(item["leaked_key"] for item in entries)
    assert len([item for item in entries if item["kind"] == "gesture"]) == 3
    assert {item["performer"] for item in entries if item["kind"] == "pcm"} == {
        "reachy", "stackchan",
    }
    for index in range(3):
        events = [
            item for item in entries
            if item.get("kind") == "presence" and item["session"] == f"signed-room-{index}"
        ]
        assert events[-1]["state"] == "disconnected"
        assert [e["sequence"] for e in events] == sorted(e["sequence"] for e in events)
        assert any(e["interrupted"] for e in events)
        assert not any(e["state"] == "speaking" for e in events[
            next(i for i, item in enumerate(events) if item["interrupted"]):
        ])


def test_following_idle_never_erases_unacknowledged_interrupt(tmp_path):
    room = EnsembleSession(
        PuppeteerEnsembleConfig(operator_config(tmp_path)),
        room_id="signed-room", caller_id="owner",
    )
    room._task = Active()
    room.observe("speaking")
    room.observe("listening", interrupted=True)
    room.observe("idle")
    pending = []
    while not room._queue.empty():
        tool, event, _ = room._queue.get_nowait()
        assert tool == "ensemble_presence"
        pending.append(event)
    assert [x["state"] for x in pending] == ["listening", "idle"]
    assert [x["interrupted"] for x in pending] == [True, False]
    assert [x["sequence"] for x in pending] == [1, 2]
