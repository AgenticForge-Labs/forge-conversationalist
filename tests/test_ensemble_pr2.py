"""PR2 deterministic no-motion lifecycle, priority and operator review tests."""

from __future__ import annotations

import asyncio
import json
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
