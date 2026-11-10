import json
import math

import pytest

from forge_conversationalist.ensemble import PuppeteerEnsembleConfig

KINDS = ("reachy", "stackchan", "soarm101")


def provider(kind, ident):
    if kind == "reachy":
        return {"backend": "reachy_sdk", "device": kind, "instance_id": ident,
                "allowed_tools": ["head_pose", "play_move", "device_stop"]}
    if kind == "stackchan":
        return {"backend": "stackchan_sdk", "device": kind, "instance_id": ident,
                "allowed_tools": ["head_move", "face_update", "device_stop"]}
    return {"instance_id": ident, "mode": "existing_broker",
            "broker_url": "http://127.0.0.1:8765", "token_env": "SOARM_TOKEN",
            "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}


def make_room(tmp_path, selected):
    refs = []
    for kind in selected:
        ident = f"robot-{kind}"
        (tmp_path / f"{kind}.json").write_text(json.dumps(
            {"kind": kind, "id": ident, "provider": provider(kind, ident)}))
        refs.append({"kind": kind, "id": ident, "file": f"{kind}.json"})
    room = {"schema_version": 4, "physical": True,
            "robots": refs, "action_journal": str(tmp_path / "actions.db"),
            "ownership_db": str(tmp_path / "owners.db")}
    if "reachy" in selected:
        room["reachy_neutral_head"] = str(tmp_path / "neutral.json")
        (tmp_path / "neutral.json").write_text(json.dumps(
            [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]))
    path = tmp_path / "room.json"
    path.write_text(json.dumps(room))
    return path, room


@pytest.mark.parametrize("selected", [
    ("reachy",), ("stackchan",), ("soarm101",),
    ("reachy", "stackchan"), ("reachy", "soarm101"),
    ("stackchan", "soarm101"), KINDS,
])
def test_v4_accepts_all_nonempty_unique_robot_subsets(tmp_path, selected):
    path, _ = make_room(tmp_path, selected)
    config = PuppeteerEnsembleConfig(path)
    assert config.schema_version == 4
    assert config.performer_ids == tuple(f"robot-{kind}" for kind in selected)
    assert config.preflight()["performer_ids"] == list(config.performer_ids)


def test_v4_rejects_duplicate_kind_id_file_mismatch_and_post_validation_edit(tmp_path):
    path, room = make_room(tmp_path, ("stackchan",))
    (tmp_path / "stackchan-2.json").write_text(json.dumps(
        {"kind": "stackchan", "id": "other", "provider": provider("stackchan", "other")}))
    room["robots"].append({"kind": "stackchan", "id": "other", "file": "stackchan-2.json"})
    path.write_text(json.dumps(room))
    with pytest.raises(ValueError, match="unique"):
        PuppeteerEnsembleConfig(path)

    path, room = make_room(tmp_path, ("stackchan",))
    room["robots"][0]["id"] = "mismatch"
    path.write_text(json.dumps(room))
    with pytest.raises(ValueError, match="identity mismatch"):
        PuppeteerEnsembleConfig(path)

    path, _ = make_room(tmp_path, ("soarm101",))
    config = PuppeteerEnsembleConfig(path)
    (tmp_path / "soarm101.json").write_text(json.dumps(
        {"kind": "soarm101", "id": "robot-soarm101", "provider": provider("soarm101", "changed")}))
    with pytest.raises(PermissionError, match="changed"):
        config.require_unchanged()


def test_v4_accepts_puppeteer_soarm_broker_config_with_exact_performer_id(tmp_path):
    ident = "room-arm"
    config_data = {"instance_id": ident, "mode": "existing_broker",
        "broker_url": "http://127.0.0.1:8765",
        "token_env": "SOARM_TOKEN",
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}
    (tmp_path / "soarm101.json").write_text(json.dumps(
        {"kind": "soarm101", "id": ident, "provider": config_data}))
    room = {"schema_version": 4,
        "physical": True,
        "robots": [{"kind": "soarm101", "id": ident, "file": "soarm101.json"}],
        "action_journal": str(tmp_path / "actions.db"),
        "ownership_db": str(tmp_path / "owners.db")}
    path = tmp_path / "room.json"
    path.write_text(json.dumps(room))
    configured = PuppeteerEnsembleConfig(path)
    assert configured.performer_ids == (ident,)
    assert configured.preflight()["performer_ids"] == [ident]


def test_v4_sdk_simulation_catalog_is_resolved_and_integrity_fenced(tmp_path):
    ident = "room-arm"
    catalog = tmp_path / "trusted-catalog.json"
    catalog.write_text('{"approved":true}')
    child = tmp_path / "arm.json"
    child.write_text(json.dumps({"kind": "soarm101", "id": ident, "provider": {
        "instance_id": ident, "mode": "sdk_simulation_daemon",
        "broker_url": "http://127.0.0.1:8765", "simulation_state_dir": "/tmp/arm",
        "simulation_port": 8765, "token_env": "SOARM_TOKEN",
        "trusted_host_token_env": "SOARM_HOST_TOKEN",
        "choreography_revisions": {"idle": "sha256:" + "a" * 64,
                                    "speaking": "sha256:" + "b" * 64},
        "choreography_durations_s": {"idle": 1, "speaking": 1},
        "trusted_choreography_catalog": "trusted-catalog.json",
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}}))
    room_path = tmp_path / "room.json"
    room_path.write_text(json.dumps({"schema_version": 4,
        "physical": True,
        "robots": [{"kind": "soarm101", "id": ident, "file": child.name}],
        "action_journal": str(tmp_path / "actions.db"),
        "ownership_db": str(tmp_path / "owners.db")}))
    configured = PuppeteerEnsembleConfig(room_path)
    catalog.write_text('{"approved":false}')
    with pytest.raises(PermissionError, match="changed"):
        configured.require_unchanged()


@pytest.mark.parametrize("startup_hold", [False, True])
def test_v4_physical_daemon_config_and_catalog_are_integrity_fenced(tmp_path, startup_hold):
    ident = "room-arm"
    catalog = tmp_path / "physical-catalog.json"
    catalog.write_text('{"catalog":1}')
    daemon = tmp_path / "physical-daemon.json"
    daemon.write_text(json.dumps({"physical": True, "robot_id": "arm-hw", "port": "/dev/arm",
        "calibration_id": "cal", "lease_path": None, "operator_choreography_only": True,
        "trusted_catalog_path": str(catalog), "loopback_port": 8765,
        "state_dir": str(tmp_path / "state"), "establish_hold_on_startup": startup_hold}))
    robot = tmp_path / "arm.json"
    robot.write_text(json.dumps({"kind": "soarm101", "id": ident, "provider": {
        "instance_id": ident, "mode": "sdk_physical_daemon",
        "broker_url": "http://127.0.0.1:8765", "token_env": "SOARM_TOKEN",
        "trusted_host_token_env": "SOARM_HOST_TOKEN",
        "physical_daemon_config": str(daemon), "startup_target_revision": "sha256:" + "c" * 64,
        "choreography_revisions": {"idle": "sha256:" + "a" * 64,
                                    "speaking": "sha256:" + "b" * 64},
        "choreography_durations_s": {"idle": 1, "speaking": 1},
        "trusted_choreography_catalog": catalog.name,
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}}))
    room_path = tmp_path / "room.json"
    room_path.write_text(json.dumps({"schema_version": 4,
        "physical": True,
        "robots": [{"kind": "soarm101", "id": ident, "file": robot.name}],
        "action_journal": str(tmp_path / "actions.db"),
        "ownership_db": str(tmp_path / "owners.db")}))
    configured = PuppeteerEnsembleConfig(room_path)
    daemon.write_text(daemon.read_text() + " ")
    with pytest.raises(PermissionError, match="changed"):
        configured.require_unchanged()


@pytest.mark.anyio
async def test_v4_ensemble_session_drives_real_puppeteer_sdk_simulation(tmp_path):
    import os
    import socket
    import sqlite3
    from pathlib import Path

    import anyio

    pytest.importorskip("mcp")
    pytest.importorskip("soarm101_motion.choreography")
    from soarm101_motion.choreography import ChoreographyPolicy, JointChoreography, JointFrame

    from forge_conversationalist.ensemble import EnsembleSession

    # Sibling checkouts next to this repository, overridable for other layouts.
    siblings = Path(__file__).resolve().parents[2]
    sdk_root = Path(os.environ.get("SOARM_SDK_ROOT", siblings / "soarm101-motion-sdk"))
    puppet_root = Path(os.environ.get("PUPPETEER_ROOT", siblings / "forge-puppeteer"))
    executable = Path(os.environ.get("SOARM_TEST_SIM_DAEMON", str(sdk_root / ".venv/bin/soarm101-sim-daemon")))
    if not executable.is_file():
        pytest.skip("local SO-ARM SDK simulation executable is unavailable")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    def frames(target, duration, frequency_hz=25):
        count = round(duration * frequency_hz)
        return tuple(JointFrame(duration * index / count,
            (target * (1 - math.cos(2 * math.pi * index / count)) / 2, 0, 0, 0, 0))
            for index in range(count + 1))

    programs = [JointChoreography.author(name, "so101", "simulation", frames(target, duration))
                for name, target, duration in (("idle", .006, 3), ("speaking", .01, 4.5), ("yes", .004, 1))]
    policy = ChoreographyPolicy("so101", "simulation", tuple(p.revision for p in programs),
        max_duration_s=6, max_speed_deg_s=8, max_acceleration_deg_s2=25)
    catalog = tmp_path / "trusted-catalog.json"
    catalog.write_text(json.dumps({"policy": {
        "robot_id": policy.robot_id, "calibration_id": policy.calibration_id,
        "allowed_revisions": list(policy.allowed_revisions), "max_duration_s": policy.max_duration_s,
        "max_samples": policy.max_samples, "max_start_error_rad": policy.max_start_error_rad,
        "max_speed_deg_s": policy.max_speed_deg_s,
        "max_acceleration_deg_s2": policy.max_acceleration_deg_s2,
        "allow_physical_trial": False,
    }, "programs": [{"name": p.name, "robot_id": p.robot_id,
        "calibration_id": p.calibration_id, "revision": p.revision,
        "frames": [{"at_s": f.at_s, "joints_rad": list(f.joints_rad)} for f in p.frames]}
        for p in programs]}))
    state = tmp_path / "sdk-state"
    robot = tmp_path / "soarm101.json"
    robot.write_text(json.dumps({"kind": "soarm101", "id": "sim-arm", "provider": {
        "instance_id": "sim-arm", "mode": "sdk_simulation_daemon",
        "broker_url": f"http://127.0.0.1:{port}", "token_env": "TEST_SOARM_TOKEN",
        "trusted_host_token_env": "TEST_SOARM_HOST_TOKEN",
        "choreography_revisions": {p.name: p.revision for p in programs},
        "choreography_durations_s": {p.name: p.frames[-1].at_s for p in programs},
        "trusted_choreography_catalog": catalog.name,
        "simulation_state_dir": str(state), "simulation_port": port,
        "sdk_simulation_executable": str(executable),
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}}))
    room_path = tmp_path / "room.json"
    room_path.write_text(json.dumps({"schema_version": 4,
        "physical": True,
        "robots": [{"kind": "soarm101", "id": "sim-arm", "file": robot.name}],
        "action_journal": str(tmp_path / "actions.db"),
        "ownership_db": str(tmp_path / "owners.db")}))

    # This is only a process entrypoint. It runs Puppeteer's actual MCP main
    # with both sibling source trees and the SDK source available explicitly.
    launcher = tmp_path / "forge-puppeteer-ensemble"
    pythonpath = os.pathsep.join(map(str, (puppet_root / "src", sdk_root / "src")))
    sdk_python = Path(os.environ.get("SOARM_TEST_PYTHON", str(sdk_root / ".venv/bin/python")))
    if os.environ.get("SOARM_TEST_INSTALLED"):
        pythonpath = ""
    launcher.write_text("#!/bin/sh\nexport PYTHONPATH=" + repr(pythonpath) + ":${PYTHONPATH:-}\n"
        + "exec " + repr(str(sdk_python)) + " -c 'from forge_puppeteer.ensemble.launcher import main; main()' \"$@\"\n")
    launcher.chmod(0o755)
    config = PuppeteerEnsembleConfig(room_path, command=str(launcher))
    room = EnsembleSession(config, room_id="sim-room", caller_id="local-test")

    def action_state(name):
        with sqlite3.connect(tmp_path / "actions.db") as db:
            rows = db.execute("SELECT request, record FROM actions").fetchall()
        matches = [(json.loads(req), json.loads(record)) for req, record in rows
                   if json.loads(req).get("asset_id", "").endswith("/" + name)]
        return matches[-1][1].get("state") if matches else None

    def completed_count(name):
        with sqlite3.connect(tmp_path / "actions.db") as db:
            rows = db.execute("SELECT request, record FROM actions").fetchall()
        return sum(1 for req, record in rows
                   if json.loads(req).get("asset_id", "").endswith("/" + name)
                   and json.loads(record).get("state") == "completed")

    async def wait_for(name, state_name):
        with anyio.fail_after(10):
            while action_state(name) != state_name:
                await anyio.sleep(.025)

    started = False
    try:
        status = await room.start()
        started = True
        assert set(status["performers"]) == {"sim-arm"}
        await wait_for("idle", "completed")
        room.observe("speaking", source="playback")
        await wait_for("speaking", "completed")
        before = completed_count("idle")
        room.observe("idle", source="playback")
        with anyio.fail_after(10):
            while completed_count("idle") <= before:
                await anyio.sleep(.025)
        result = await room.express("yes", "sim-arm")
        assert result
        await wait_for("yes", "completed")
        closed = await room.close()
        assert closed["outcome"] == "acknowledged"
    finally:
        if not started or room.shutdown_evidence() is None:
            await room.close()
        with anyio.fail_after(5):
            while (state / "daemon.json").exists():
                await anyio.sleep(.025)
    assert not (state / "broker.token").exists()
    assert not (state / "trusted-host.token").exists()
    assert not (state / "daemon.json").exists()


def _options_room(tmp_path, reachy_options=None, stackchan_options=None, *, physical=True):
    for kind, options in (("reachy", reachy_options), ("stackchan", stackchan_options)):
        body = {"kind": kind, "id": kind, "provider": provider(kind, kind)}
        if kind == "reachy":
            body["provider"]["allowed_tools"] += ["wake_up", "original_idle", "pose_and_antennas"]
        if options is not None:
            body["options"] = options
        (tmp_path / f"{kind}.json").write_text(json.dumps(body))
    (tmp_path / "neutral.json").write_text(json.dumps(
        [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]))
    room = {"schema_version": 4, "physical": physical,
            "robots": [{"kind": k, "id": k, "file": f"{k}.json"} for k in ("reachy", "stackchan")],
            "action_journal": str(tmp_path / "actions.db"), "ownership_db": str(tmp_path / "owners.db"),
            "reachy_neutral_head": str(tmp_path / "neutral.json")}
    path = tmp_path / "ensemble.json"
    path.write_text(json.dumps(room))
    return path


def test_v4_options_set_motion_profiles_and_speech_driven_performers(tmp_path):
    path = _options_room(
        tmp_path,
        {"idle": "original_v1", "speaking": "smooth_v2", "speech_driven": True,
         "legacy_speech_fallback": True, "neutral_antennas_rad": [-0.17, 0.17]},
        {"speaking_mode": "native_weight", "speech_driven": True})
    config = PuppeteerEnsembleConfig(path)
    assert config.motion_profiles == {"reachy": {"idle": "original_v1", "speaking": "smooth_v2"}}
    assert config.speech_driven_performers == ("reachy", "stackchan")
    summary = config.preflight()
    assert summary["motion_profiles"] == {"reachy": {"idle": "original_v1", "speaking": "smooth_v2"}}
    assert summary["speech_driven_performers"] == ["reachy", "stackchan"]


def test_v4_without_options_has_no_profiles_or_speech_driven_performers(tmp_path):
    config = PuppeteerEnsembleConfig(_options_room(tmp_path))
    assert config.motion_profiles == {} and config.speech_driven_performers == ()


@pytest.mark.parametrize("reachy,stackchan,message", [
    ({"speaking": "smooth_v2"}, None, "speech-driven"),
    ({"legacy_speech_fallback": True}, None, "legacy speech fallback"),
    ({"idle": "native_v1"}, None, "invalid Puppeteer v4 Reachy option"),
    ({"surprise": 1}, None, "unknown Puppeteer v4 Reachy options"),
    ({"speech_driven": "yes"}, None, "must be true or false"),
    (None, {"speaking_mode": "loud"}, "StackChan options"),
])
def test_v4_rejects_invalid_options(tmp_path, reachy, stackchan, message):
    with pytest.raises(ValueError, match=message):
        PuppeteerEnsembleConfig(_options_room(tmp_path, reachy, stackchan))


def test_v4_physical_flag_replaces_the_authorization_marker(tmp_path):
    # Real robots need physical: true; the retired markers are rejected, not ignored.
    with pytest.raises(ValueError, match="physical: true"):
        PuppeteerEnsembleConfig(_options_room(tmp_path, physical=False))
    path = _options_room(tmp_path)
    data = json.loads(path.read_text())
    data["motion_authority"] = "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unknown Puppeteer v4 room fields"):
        PuppeteerEnsembleConfig(path)
    path = _options_room(tmp_path)
    reachy = json.loads((tmp_path / "reachy.json").read_text())
    reachy["provider"]["physical_authority"] = "I_AUTHORIZE_REACHY_CONTROL"
    (tmp_path / "reachy.json").write_text(json.dumps(reachy))
    with pytest.raises(ValueError, match="unknown Puppeteer v4 SDK provider fields"):
        PuppeteerEnsembleConfig(path)


def test_v4_soarm_sleep_fields_are_validated(tmp_path):
    ident = "arm"
    base = {"instance_id": ident, "mode": "existing_broker", "broker_url": "http://127.0.0.1:8765",
            "token_env": "SOARM_TOKEN", "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}

    def room(**extra):
        (tmp_path / "arm.json").write_text(json.dumps(
            {"kind": "soarm101", "id": ident, "provider": {**base, **extra}}))
        path = tmp_path / "ensemble.json"
        path.write_text(json.dumps({"schema_version": 4, "physical": False,
            "robots": [{"kind": "soarm101", "id": ident, "file": "arm.json"}],
            "action_journal": str(tmp_path / "a.db"), "ownership_db": str(tmp_path / "o.db")}))
        return path

    assert PuppeteerEnsembleConfig(room(sleep_check=False)).performer_ids == (ident,)
    with pytest.raises(ValueError, match="sleep_target"):
        PuppeteerEnsembleConfig(room(sleep_target="sleep2"))
    with pytest.raises(ValueError, match="sleep_check"):
        PuppeteerEnsembleConfig(room(sleep_check="no"))
    pin = "sha256:" + "d" * 64
    ok = room(sleep_target="sleep2", pose_targets={"sleep2": pin}, trusted_host_token_env="SOARM_HOST")
    assert PuppeteerEnsembleConfig(ok).performer_ids == (ident,)
