"""Real MCP stdio transport for paced content-free native gain observations."""

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

from forge_conversationalist.ensemble import EnsembleSession, PuppeteerEnsembleConfig


def frame(sequence):
    return {"schema": "forge.native_speech_gain.v1", "stream_id": "tts-1",
            "sequence": sequence, "offset_ms": sequence * 50, "duration_ms": 50,
            "gain": 0.7, "timing_source": "generated_pcm", "acoustic_playout_measured": False}


def setup_room(tmp_path, *, reject=False, delay=0, malformed=False, presence_delay=0):
    log = tmp_path / "events.jsonl"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "schema_version": 2, "motion_authority": "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION",
        "reachy": {"instance_id": "reachy", "backend": "reachy_sdk", "device": "reachy"},
        "reachy_neutral_head": str(tmp_path / "neutral.json"),
        "action_journal": str(tmp_path / "actions.sqlite"),
        "ownership_db": str(tmp_path / "owners.sqlite"),
        "test_log": str(log), "test_reject_native": reject, "test_native_delay_s": delay,
        "test_malformed_native": malformed, "test_presence_delay_s": presence_delay,
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
async def test_identified_output_event_arms_same_room_owned_child(tmp_path):
    from forge_conversationalist.identified_playback import IdentifiedPlaybackBinding

    class Output:
        identified_playback_supported = True

        def on(self, _event, callback):
            self.callback = callback

        def off(self, _event, callback):
            assert callback == self.callback

    room, log = setup_room(tmp_path)
    await room.start()
    try:
        assert room.begin_native_gain("tts-1")
        assert room.offer_native_gain([frame(i) for i in range(8)])
        room.observe("speaking", source="playback")
        output = Output()
        binding = IdentifiedPlaybackBinding(room.associate_native_playback)
        assert binding.attach(output)
        output.callback(SimpleNamespace(segment_id="other", monotonic_ns=time.monotonic_ns()))
        await asyncio.sleep(0.08)
        assert not [event for event in entries(log) if event["kind"] == "gain"]
        output.callback(SimpleNamespace(segment_id="tts-1", monotonic_ns=time.monotonic_ns()))
        await asyncio.sleep(0.2)
        assert any(event["kind"] == "gain" for event in entries(log))
        binding.detach()
    finally:
        await room.close()


@pytest.mark.anyio
async def test_tagged_tts_through_capable_livekit_output_and_fake_mcp(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from livekit import rtc
    from livekit.agents.voice.generation import perform_audio_forwarding
    from livekit.agents.voice.room_io import _output

    from forge_conversationalist.identified_playback import IdentifiedSpeechBridge
    from forge_conversationalist.speech_envelope_tap import observe_synthesized_audio

    if not hasattr(_output._ParticipantAudioOutput, "identified_playback_supported"):
        pytest.skip("installed LiveKit has no identified output capability")

    class FakeAudioSource:
        queued_duration = 0.0

        def __init__(self, *_args, **_kwargs):
            pass

        async def capture_frame(self, _frame):
            pass

        def clear_queue(self):
            pass

        async def wait_for_playout(self):
            await asyncio.Event().wait()

    monkeypatch.setattr(_output.rtc, "AudioSource", FakeAudioSource)
    output = _output._ParticipantAudioOutput(
        MagicMock(), sample_rate=48000, num_channels=1, track_publish_options=MagicMock(),
    )
    output._subscribed_fut.set_result(None)
    forwarder = asyncio.create_task(output._forward_audio())
    room, log = setup_room(tmp_path)
    await room.start()
    bridge = IdentifiedSpeechBridge(room)
    assert bridge.attach(output)
    output.on("playback_started", lambda _event: room.observe("speaking", source="playback"))
    playback_started = asyncio.Event()
    output.on("identified_playback_started", lambda _event: playback_started.set())

    async def source():
        # Output starts before 50 ms of PCM has produced the first feature.
        for _ in range(2):
            yield rtc.AudioFrame(bytes(960), 24000, 1, 480)
        await asyncio.wait_for(playback_started.wait(), timeout=2)
        for _ in range(98):
            yield rtc.AudioFrame(bytes(960), 24000, 1, 480)

    try:
        audio = observe_synthesized_audio(
            source(), lambda _frames: True, stream_id="tts-output-test", native_sink=bridge.offer,
            tag_native_output=True, native_begin=bridge.begin, native_abort=bridge.abort,
        )
        task, _result = perform_audio_forwarding(
            audio_output=output, tts_output=audio, reconcile_playout_pause=lambda: None,
        )
        await task
        await asyncio.sleep(0.35)
        gains = [event for event in entries(log) if event["kind"] == "gain"]
        count = sum(len(event["sequences"]) for event in gains)
        assert 3 <= count < 20
        assert {event["stream"] for event in gains} == {"tts-output-test"}
        room.observe("listening", interrupted=True)
        await room.status()
        before = len([event for event in entries(log) if event["kind"] == "gain"])
        await asyncio.sleep(0.15)
        assert len([event for event in entries(log) if event["kind"] == "gain"]) == before
    finally:
        bridge.detach()
        forwarder.cancel()
        await asyncio.gather(forwarder, return_exceptions=True)
        if output._flush_task is not None:
            output._flush_task.cancel()
            await asyncio.gather(output._flush_task, return_exceptions=True)
        await room.close()


@pytest.mark.anyio
async def test_identified_output_drives_original_motion_via_real_fake_puppeteer(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from livekit import rtc
    from livekit.agents.voice.generation import perform_audio_forwarding
    from livekit.agents.voice.room_io import _output

    from forge_conversationalist.identified_playback import IdentifiedSpeechBridge
    from forge_conversationalist.speech_envelope_tap import observe_synthesized_audio

    if not hasattr(_output._ParticipantAudioOutput, "identified_playback_supported"):
        pytest.skip("installed LiveKit has no identified output capability")
    spec = importlib.util.find_spec("forge_puppeteer")
    if spec is None or spec.origin is None:
        pytest.skip("editable Puppeteer checkout unavailable")
    puppet_root = Path(spec.origin).resolve().parents[2]
    fixture = puppet_root / "tests/robots/mcp_original_idle_fake_server.py"
    puppet_python = puppet_root / ".venv/bin/python"
    if not fixture.is_file():
        pytest.skip("Puppeteer fake-device integration fixture unavailable")
    if not puppet_python.is_file():
        pytest.skip("Puppeteer SDK test environment unavailable")
    target_log = tmp_path / "targets.txt"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "schema_version": 2, "motion_authority": "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION",
        "reachy": {"instance_id": "reachy", "backend": "reachy_sdk", "device": "reachy"},
        "reachy_neutral_head": str(tmp_path / "neutral.json"),
        "action_journal": str(tmp_path / "actions.sqlite"),
        "ownership_db": str(tmp_path / "owners.sqlite"),
    }))
    launcher = tmp_path / "forge-puppeteer-ensemble"
    launcher.write_text(
        f"#!{puppet_python}\nimport sys\nsys.argv = ['fixture', {str(target_log)!r}, 'speech']\n"
        f"exec(compile(open({str(fixture)!r}).read(), {str(fixture)!r}, 'exec'))\n"
    )
    launcher.chmod(0o755)
    room = EnsembleSession(PuppeteerEnsembleConfig(config_path, command=str(launcher)),
                           room_id="room", caller_id="owner", native_gain_performer="reachy")

    class FakeAudioSource:
        queued_duration = 0.0

        def __init__(self, *_args, **_kwargs):
            pass

        async def capture_frame(self, _frame):
            pass

        async def wait_for_playout(self):
            await asyncio.Event().wait()

        def clear_queue(self):
            pass

    monkeypatch.setattr(_output.rtc, "AudioSource", FakeAudioSource)
    output = _output._ParticipantAudioOutput(
        MagicMock(), sample_rate=48000, num_channels=1, track_publish_options=MagicMock(),
    )
    output._subscribed_fut.set_result(None)
    forwarder = asyncio.create_task(output._forward_audio())
    await room.start()
    bridge = IdentifiedSpeechBridge(room)
    assert bridge.attach(output)
    output.on("playback_started", lambda _event: room.observe("speaking", source="playback"))

    async def source():
        from array import array

        for _ in range(40):
            yield rtc.AudioFrame(array("h", [12000] * 1200).tobytes(), 24000, 1, 1200)

    try:
        room.observe("idle")
        with anyio.fail_after(3):
            while not target_log.exists() or "idle" not in target_log.read_text():
                await asyncio.sleep(0.01)
        audio = observe_synthesized_audio(
            source(), lambda _frames: True, stream_id="tts-real-puppet", native_sink=bridge.offer,
            tag_native_output=True, native_begin=bridge.begin, native_abort=bridge.abort,
        )
        task, _ = perform_audio_forwarding(
            audio_output=output, tts_output=audio, reconcile_playout_pause=lambda: None,
        )
        await task
        with anyio.fail_after(3):
            while "speech" not in target_log.read_text().splitlines():
                await asyncio.sleep(0.02)
        room.observe("listening", interrupted=True)
        await room.status()
        count = len(target_log.read_text().splitlines())
        await asyncio.sleep(0.1)
        assert len(target_log.read_text().splitlines()) == count
    finally:
        bridge.detach()
        forwarder.cancel()
        await asyncio.gather(forwarder, return_exceptions=True)
        if output._flush_task is not None:
            output._flush_task.cancel()
            await asyncio.gather(output._flush_task, return_exceptions=True)
        await room.close()


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
async def test_normal_segment_end_preserves_prefetch_and_releases_slots(tmp_path):
    pytest.importorskip("mcp")
    room, _ = setup_room(tmp_path)
    await room.start()
    try:
        assert room.begin_native_gain("tts-0")
        assert room.begin_native_gain("tts-1")
        for i in range(6):
            stream = f"tts-{i}"
            assert room.offer_native_gain([{**frame(0), "stream_id": stream}])
            room.observe("speaking", source="playback")
            assert room.associate_native_playback(stream, output_started_at=time.monotonic())
            await asyncio.sleep(0.07)
            room.observe("listening", source="playback")
            await room.status()
            if i + 2 < 6:
                assert room.begin_native_gain(f"tts-{i + 2}")
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
async def test_cancelled_analysis_cannot_arm_a_partly_buffered_segment(tmp_path):
    pytest.importorskip("mcp")
    room, log = setup_room(tmp_path)
    await room.start()
    try:
        assert room.begin_native_gain("tts-1")
        assert room.offer_native_gain([frame(0)])
        room.cancel_native_gain("tts-1")
        room.observe("speaking", source="playback")
        assert not room.associate_native_playback("tts-1", output_started_at=time.monotonic())
        assert not room.begin_native_gain("tts-1")
        await asyncio.sleep(0.1)
        assert not [item for item in entries(log) if item["kind"] == "gain"]
    finally:
        await room.close()


@pytest.mark.anyio
async def test_delayed_presence_ack_drops_old_features_but_keeps_speaking(tmp_path):
    pytest.importorskip("mcp")
    room, log = setup_room(tmp_path, presence_delay=0.35)
    await room.start()
    try:
        assert room.begin_native_gain("tts-1")
        for start in (0, 8, 16):
            assert room.offer_native_gain([frame(i) for i in range(start, start + 8)])
        room.observe("speaking", source="playback")
        assert room.associate_native_playback("tts-1", output_started_at=time.monotonic())
        await asyncio.sleep(0.75)
        gains = [item for item in entries(log) if item["kind"] == "gain"]
        assert len(gains) >= 2
        assert gains[0]["sequences"][0] >= 4
        assert all(item["stream"] == "tts-1" for item in gains)
        assert not room._native_pacer.fenced
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
        assert room.begin_native_gain("tts-late")
        assert room.offer_native_gain([{**frame(i), "stream_id": "tts-late"} for i in range(8)])
        assert room.associate_native_playback("tts-late", output_started_at=time.monotonic() - 0.25)
        await asyncio.sleep(0.12)
        late = (await room.status())["native_speech_gain"]["reachy"]
        assert late["stream_id"] == "tts-late" and late["last_sequence"] >= 3
        room.observe("listening", interrupted=True)
        assert (await room.status())["native_speech_gain"]["reachy"]["available"] is False
    finally:
        assert (await room.close())["outcome"] == "acknowledged"


@pytest.fixture
def anyio_backend():
    return "asyncio"
