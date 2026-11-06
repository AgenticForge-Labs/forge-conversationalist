"""Real MCP stdio transport for paced content-free native gain observations."""

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

from forge_conversationalist.ensemble import EnsembleSession, PuppeteerEnsembleConfig


def frame(sequence):
    return {"schema": "forge.native_speech_gain.v1", "stream_id": "tts-1",
            "sequence": sequence, "offset_ms": sequence * 50, "duration_ms": 50,
            "gain": 0.7, "timing_source": "generated_pcm", "acoustic_playout_measured": False}


def setup_room(tmp_path, *, reject=False, delay=0, malformed=False):
    log = tmp_path / "events.jsonl"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "schema_version": 2, "motion_authority": "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION",
        "reachy": {"instance_id": "reachy", "backend": "reachy_sdk", "device": "reachy"},
        "reachy_neutral_head": str(tmp_path / "neutral.json"),
        "action_journal": str(tmp_path / "actions.sqlite"),
        "ownership_db": str(tmp_path / "owners.sqlite"),
        "test_log": str(log), "test_reject_native": reject, "test_native_delay_s": delay,
        "test_malformed_native": malformed,
    }))
    launcher = tmp_path / "forge-puppeteer-ensemble"
    fixture = Path(__file__).with_name("native_gain_mcp_server.py")
    launcher.write_text(f"#!{sys.executable}\nexec(compile(open({str(fixture)!r}).read(), {str(fixture)!r}, 'exec'))\n")
    launcher.chmod(0o755)
    config = PuppeteerEnsembleConfig(config_path, command=str(launcher))
    return EnsembleSession(config, room_id="room", caller_id="owner",
                           native_gain_performer="reachy"), log


def entries(log):
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.mark.anyio
async def test_prefetched_segment_does_not_cancel_current_output(tmp_path):
    pytest.importorskip("mcp")
    room, log = setup_room(tmp_path)
    await room.start()
    try:
        assert room.begin_native_gain("tts-1")
        assert room.offer_native_gain([frame(i) for i in range(8)])
        room.observe("speaking", source="playback")
        assert room.associate_native_playback("tts-1", output_started_at=time.monotonic() + 0.05)
        assert room.begin_native_gain("tts-2")
        second = [{**frame(i), "stream_id": "tts-2"} for i in range(8)]
        assert room.offer_native_gain(second)
        await asyncio.sleep(0.25)
        first_emitted = [entry for entry in entries(log) if entry["kind"] == "gain"]
        assert len(first_emitted) >= 2
        assert {entry["stream"] for entry in first_emitted} == {"tts-1"}
        assert not room._native_pending["tts-1"].fenced
        # Association, not TTS generation, ends the earlier segment.
        assert room.associate_native_playback("tts-2", output_started_at=time.monotonic() + 0.05)
        assert "tts-1" not in room._native_pending
        await asyncio.sleep(0.2)
        emitted = [entry for entry in entries(log) if entry["kind"] == "gain"]
        assert len(emitted) > len(first_emitted)
        assert any(entry["stream"] == "tts-2" for entry in emitted)
        room.observe("listening", interrupted=True)
        await room.status()
        assert not room._native_pending
    finally:
        await room.close()


@pytest.mark.anyio
async def test_prefetch_is_bounded_and_interruption_discards_all_segments(tmp_path):
    pytest.importorskip("mcp")
    room, _ = setup_room(tmp_path)
    await room.start()
    try:
        for i in range(4):
            stream = f"tts-{i}"
            assert room.begin_native_gain(stream)
            assert room.offer_native_gain([{**frame(0), "stream_id": stream}])
        assert not room.begin_native_gain("tts-4")
        room.observe("listening", interrupted=True)
        assert not room._native_pending
        assert not room.offer_native_gain([{**frame(1), "stream_id": "tts-3"}])
        assert not room.associate_native_playback("tts-3", output_started_at=time.monotonic())
        assert not room.begin_native_gain("tts-3")
    finally:
        await room.close()


@pytest.mark.anyio
async def test_native_gain_is_paced_on_same_child_and_stops_at_interruption(tmp_path):
    pytest.importorskip("mcp")
    room, log = setup_room(tmp_path)
    await room.start()
    try:
        assert room.begin_native_gain("tts-1")
        for i in range(0, 16, 8):
            assert room.offer_native_gain([frame(n) for n in range(i, i + 8)])
        room.observe("speaking", source="playback")
        assert room.associate_native_playback("tts-1", output_started_at=time.monotonic() + 0.05)
        await asyncio.sleep(0.48)
        emitted = [entry for entry in entries(log) if entry["kind"] == "gain"]
        sequences = [n for item in emitted for n in item["sequences"]]
        assert sequences == list(range(len(sequences)))
        assert 6 <= len(sequences) <= 10
        assert emitted[-1]["time"] - emitted[0]["time"] >= 0.20
        assert all(item["performer"] == "reachy" for item in emitted)
        assert all(item["keys"] == ["acoustic_playout_measured", "duration_ms", "gain", "offset_ms",
                                    "schema", "sequence", "stream_id", "timing_source"]
                   for item in emitted)
        room.observe("listening", interrupted=True)
        await room.status()  # priority interruption has reached the same child
        count = len([item for item in entries(log) if item["kind"] == "gain"])
        await asyncio.sleep(0.15)
        assert len([item for item in entries(log) if item["kind"] == "gain"]) == count
        assert not room.begin_native_gain("tts-1")
        assert not room.offer_native_gain([frame(16)])
    finally:
        assert (await room.close())["outcome"] == "acknowledged"


@pytest.mark.anyio
async def test_native_gain_denial_fences_stream_without_disabling_voice(tmp_path):
    pytest.importorskip("mcp")
    room, log = setup_room(tmp_path, reject=True)
    await room.start()
    try:
        assert room.begin_native_gain("tts-1")
        assert room.offer_native_gain([frame(i) for i in range(8)])
        room.observe("speaking", source="playback")
        assert room.associate_native_playback("tts-1", output_started_at=time.monotonic() + 0.05)
        await asyncio.sleep(0.3)
        assert len([entry for entry in entries(log) if entry["kind"] == "gain"]) == 1
        assert room._native_pacer.fenced
        assert (await room.status())["schema"] == "forge.behavior.v1"
    finally:
        assert (await room.close())["outcome"] == "acknowledged"


@pytest.mark.anyio
async def test_native_gain_timeout_does_not_retry_or_disable_voice(tmp_path):
    pytest.importorskip("mcp")
    room, log = setup_room(tmp_path, delay=0.5)
    await room.start()
    try:
        assert room.begin_native_gain("tts-1")
        assert room.offer_native_gain([frame(i) for i in range(8)])
        room.observe("speaking", source="playback")
        assert room.associate_native_playback("tts-1", output_started_at=time.monotonic() + 0.05)
        await asyncio.sleep(0.7)
        assert len([entry for entry in entries(log) if entry["kind"] == "gain"]) == 1
        assert room._native_pacer.fenced
        assert (await room.status())["schema"] == "forge.behavior.v1"
    finally:
        assert (await room.close())["outcome"] == "acknowledged"


@pytest.mark.anyio
async def test_malformed_native_acknowledgment_fences_the_stream(tmp_path):
    pytest.importorskip("mcp")
    room, log = setup_room(tmp_path, malformed=True)
    await room.start()
    try:
        assert room.begin_native_gain("tts-1")
        assert room.offer_native_gain([frame(i) for i in range(8)])
        room.observe("speaking", source="playback")
        assert room.associate_native_playback("tts-1", output_started_at=time.monotonic() + 0.05)
        await asyncio.sleep(0.3)
        assert len([entry for entry in entries(log) if entry["kind"] == "gain"]) == 1
        assert room._native_pacer.fenced
    finally:
        assert (await room.close())["outcome"] == "acknowledged"


@pytest.mark.anyio
async def test_native_gain_reaches_actual_puppeteer_receiver_with_fake_devices(tmp_path):
    pytest.importorskip("mcp")
    spec = importlib.util.find_spec("forge_puppeteer")
    if spec is None or spec.origin is None:
        pytest.skip("editable Puppeteer checkout unavailable")
    root = Path(spec.origin).resolve().parents[2]
    fixture = root / "tests/robots/mcp_fake_ensemble_server.py"
    if not fixture.is_file():
        pytest.skip("Puppeteer fake-device integration fixture unavailable")
    config = {
        "schema_version": 1, "motion_authority": "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION",
        "reachy": {"instance_id": "reachy", "backend": "reachy_sdk", "device": "reachy"},
        "stackchan": {"instance_id": "stackchan", "backend": "stackchan_sdk", "device": "stackchan"},
        "soarm101": {"instance_id": "soarm101", "mode": "simulation"},
        "reachy_neutral_head": str(tmp_path / "neutral.json"),
        "action_journal": str(tmp_path / "actions.sqlite"),
        "ownership_db": str(tmp_path / "owners.sqlite"),
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    launcher = tmp_path / "forge-puppeteer-ensemble"
    launcher.write_text(f"#!{sys.executable}\nexec(compile(open({str(fixture)!r}).read(), {str(fixture)!r}, 'exec'))\n")
    launcher.chmod(0o755)
    room = EnsembleSession(PuppeteerEnsembleConfig(config_path, command=str(launcher)),
                           room_id="room", caller_id="owner", native_gain_performer="reachy")
    await room.start()
    try:
        assert room.begin_native_gain("tts-1")
        assert room.offer_native_gain([frame(i) for i in range(8)])
        room.observe("speaking", source="playback")
        assert room.associate_native_playback("tts-1", output_started_at=time.monotonic() + 0.05)
        await asyncio.sleep(0.28)
        received = (await room.status())["native_speech_gain"]["reachy"]
        assert received["available"] is True
        assert received["last_sequence"] >= 2
        assert received["timing_source"] == "generated_pcm"
        assert received["physical_motion_dispatched"] is False
        room.observe("listening", interrupted=True)
        assert (await room.status())["native_speech_gain"]["reachy"]["available"] is False
    finally:
        assert (await room.close())["outcome"] == "acknowledged"


@pytest.fixture
def anyio_backend():
    return "asyncio"
