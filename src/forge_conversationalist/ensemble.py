"""One room-owned native Puppeteer ensemble MCP client.

LiveKit remains the audio/turn owner. This module emits only content-free
approximate presence and bounded named expression intent. It does not
import robot SDKs, open motor ports, select audio output or grant motion.
Puppeteer is the physical authority. There is exactly one MCP child per room.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal
from urllib.parse import urlsplit

from .native_speech_pacing import NativeSpeechPacer

logger = logging.getLogger(__name__)
SCHEMA = "forge.behavior.v1"
TOOLS = frozenset({"ensemble_state", "ensemble_presence", "ensemble_expression"})
STATES = frozenset({"idle", "listening", "speaking"})
# Each performer may hold at most this many pending envelope batches, so one
# robot's fast stream cannot starve another's, and urgent presence keeps the
# rest of the shared queue (102 of 128 entries) for STOP.
SPEECH_ENVELOPE_MAX_QUEUE = 12
MCP_EVENT_TIMEOUT_S = 15.0
SHUTDOWN_ACK_TIMEOUT_S = 8.0
# A sleep move can take as long as the SDK's longest pinned segment (30 s) plus a re-check.
MCP_PREPARE_TIMEOUT_S = 60.0
PARK_TIMEOUT_S = 60.0
STARTUP_READY_TIMEOUT_S = 45.0
STARTUP_POLL_S = 0.25
PREPARE_ACTIONS = frozenset({"move_to_sleep", "confirm_at_sleep"})
# An optional feature observation must never monopolize the one room MCP
# pipe while a live user can interrupt speech and require Puppeteer's STOP.
MCP_ENVELOPE_TIMEOUT_S = 0.35


def _presence_timing_fields(args: dict) -> dict[str, object]:
    """Content-free identity fields shared by queue and MCP timing records."""
    return {
        "session_id": args.get("session_id"),
        "event_id": args.get("event_id"),
        "sequence": args.get("sequence"),
        "state": args.get("state"),
        "source": args.get("source"),
        "interrupted": args.get("interrupted"),
    }


@dataclass(frozen=True)
class PuppeteerEnsembleConfig:
    """Operator-owned exact native performer selection for an attended room."""

    config_path: Path
    command: str = "forge-puppeteer-ensemble"
    performer_ids: tuple[str, ...] = field(init=False)
    schema_version: int = field(init=False)
    motion_profiles: dict[str, dict[str, str]] = field(init=False)
    speech_driven_performers: tuple[str, ...] = field(init=False)
    digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.config_path.is_absolute() or not self.config_path.is_file():
            raise ValueError("PUPPETEER_ENSEMBLE_CONFIG must be an existing absolute file")
        if self.command != "forge-puppeteer-ensemble":
            path = Path(self.command)
            if (
                not path.is_absolute()
                or path.name != "forge-puppeteer-ensemble"
                or not path.is_file()
                or not os.access(path, os.X_OK)
            ):
                raise ValueError("PUPPETEER_ENSEMBLE_COMMAND must be an executable Puppeteer launcher")
        source = self.config_path.read_bytes()
        try:
            data = json.loads(source)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("PUPPETEER_ENSEMBLE_CONFIG must contain valid UTF-8 JSON") from exc
        if not isinstance(data, dict):
            raise TypeError("PUPPETEER_ENSEMBLE_CONFIG must contain an object")
        if type(data.get("schema_version")) is not int or data["schema_version"] != 4:
            raise ValueError(
                "Puppeteer ensemble config must be schema version 4 (convert older files with "
                "forge-puppeteer's scripts/migrate-room-config.py)")
        ids, robot_data = self._read_v4_robot_files(data)
        if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
            raise ValueError("Puppeteer v4 room must pin unique performer identities")
        profiles, driven = self._v4_profiles(robot_data)
        object.__setattr__(self, "performer_ids", tuple(ids))
        object.__setattr__(self, "schema_version", 4)
        object.__setattr__(self, "motion_profiles", profiles)
        object.__setattr__(self, "speech_driven_performers", driven)
        # Include referenced robot files in integrity fencing, so changing
        # an ID, provider, or grant after preflight invalidates the room.
        digest_source = source + b"\0" + b"\0".join(
            self._v4_referenced_bytes(ref) for ref in data["robots"]
        )
        object.__setattr__(self, "digest", hashlib.sha256(digest_source).hexdigest())

    def require_unchanged(self) -> None:
        """Fail before starting the child if an approved config changed on disk."""
        source = self.config_path.read_bytes()
        data = json.loads(source)
        source += b"\0" + b"\0".join(
            self._v4_referenced_bytes(ref) for ref in data["robots"]
        )
        if hashlib.sha256(source).hexdigest() != self.digest:
            raise PermissionError("Puppeteer ensemble operator config changed since validation")

    _REACHY_OPTION_KEYS: ClassVar[dict[str, set[str]]] = {
        "expression_style": {"classic", "expressive"}, "startup_behavior": {"none", "wake_up_if_sleeping"},
        "listening_mode": {"tilt", "hold"}, "speaking_axis": {"yaw", "tilt"},
        "idle": {"default", "original_v1"}, "speaking": {"default", "smooth_v2"},
    }
    _REACHY_NUMBER_KEYS: ClassVar[set[str]] = {"listening_tilt_rad", "speaking_amplitude_rad", "expressive_amplitude_rad",
                           "antenna_sway_rad", "breath_amplitude_m"}

    @classmethod
    def _check_v4_options(cls, kind: str, options: object) -> None:
        """Static mirror of Puppeteer's v4 options: known keys and shapes only."""
        if not isinstance(options, dict):
            raise TypeError("Puppeteer v4 options must be an object")
        booleans = {"speech_driven"}
        if kind == "stackchan":
            if set(options) - {"speaking_mode", "speech_driven"} or options.get(
                    "speaking_mode", "legacy_size") not in {"legacy_size", "native_weight"}:
                raise ValueError("invalid Puppeteer v4 StackChan options")
        else:
            booleans |= {"legacy_speech_fallback"}
            known = (set(cls._REACHY_OPTION_KEYS) | cls._REACHY_NUMBER_KEYS | booleans
                     | {"neutral_antennas_rad"})
            if set(options) - known:
                raise ValueError("unknown Puppeteer v4 Reachy options")
            for key, allowed in cls._REACHY_OPTION_KEYS.items():
                if key in options and options[key] not in allowed:
                    raise ValueError(f"invalid Puppeteer v4 Reachy option {key}")
            for key in cls._REACHY_NUMBER_KEYS & set(options):
                if type(options[key]) not in (int, float) or not math.isfinite(options[key]):
                    raise ValueError(f"invalid Puppeteer v4 Reachy option {key}")
            antennas = options.get("neutral_antennas_rad")
            if antennas is not None and (not isinstance(antennas, list) or len(antennas) != 2 or any(
                    type(v) not in (int, float) or not math.isfinite(v) for v in antennas)):
                raise ValueError("invalid Puppeteer v4 Reachy option neutral_antennas_rad")
            if options.get("speaking") == "smooth_v2" and options.get("speech_driven") is not True:
                raise ValueError("smooth_v2 speaking requires explicit speech-driven performer")
            if options.get("legacy_speech_fallback") is True and (
                    options.get("speaking") != "smooth_v2" or options.get("speech_driven") is not True
                    or options.get("expression_style", "classic") != "classic"):
                raise ValueError("legacy speech fallback requires smooth_v2, speech-driven Reachy and classic style")
        for key in booleans & set(options):
            if type(options[key]) is not bool:
                raise ValueError(f"Puppeteer v4 option {key} must be true or false")

    @staticmethod
    def _check_v4_grants(kind: str, options: dict, grants: list) -> None:
        """Options that need an explicit SDK tool grant (mirrors Puppeteer's load-time check)."""
        if kind != "reachy":
            return
        tools = set(grants)
        if options.get("startup_behavior") == "wake_up_if_sleeping" and "wake_up" not in tools:
            raise ValueError("Reachy wake-up startup requires the wake_up grant")
        if options.get("idle") == "original_v1" and "original_idle" not in tools:
            raise ValueError("original_v1 idle requires original_idle grant")
        if options.get("expression_style") == "expressive" or options.get("speaking") == "smooth_v2":
            if "pose_and_antennas" not in tools:
                raise ValueError("expressive Reachy motion requires the pose_and_antennas grant")
            if options.get("neutral_antennas_rad") is None:
                raise ValueError("expressive Reachy motion requires reviewed neutral_antennas_rad")

    @staticmethod
    def _v4_profiles(robots) -> tuple[dict[str, dict[str, str]], tuple[str, ...]]:
        """Motion profiles and speech-driven performers, derived from robot options."""
        profiles: dict[str, dict[str, str]] = {}
        driven: list[str] = []
        for robot in robots:
            options = robot.get("options") or {}
            if robot["kind"] == "reachy":
                idle = "original_v1" if options.get("idle") == "original_v1" else "legacy"
                speaking = "smooth_v2" if options.get("speaking") == "smooth_v2" else "legacy"
                if idle != "legacy" or speaking != "legacy":
                    profiles[robot["id"]] = {"idle": idle, "speaking": speaking}
            if robot["kind"] != "soarm101" and options.get("speech_driven") is True:
                driven.append(robot["id"])
        return profiles, tuple(driven)

    def _v4_referenced_bytes(self, ref: dict) -> bytes:
        """Hash robot config and every SDK-owned daemon config/catalog it references."""
        robot_path = Path(ref["file"])
        if not robot_path.is_absolute():
            robot_path = self.config_path.parent / robot_path
        robot_path = robot_path.resolve()
        robot_bytes = robot_path.read_bytes()
        try:
            robot = json.loads(robot_bytes)
            provider = robot.get("provider", {}) if isinstance(robot, dict) else {}
            catalog = provider.get("trusted_choreography_catalog") if isinstance(provider, dict) else None
            daemon_config = provider.get("physical_daemon_config") if isinstance(provider, dict) else None
            referenced = []
            if daemon_config:
                config_path = Path(daemon_config)
                if not config_path.is_absolute():
                    config_path = robot_path.parent / config_path
                config_bytes = config_path.resolve().read_bytes()
                referenced.append(config_bytes)
                try:
                    daemon_data = json.loads(config_bytes)
                    daemon_catalog = daemon_data.get("trusted_catalog_path")
                    if daemon_catalog:
                        referenced.append(Path(daemon_catalog).resolve().read_bytes())
                except (ValueError, TypeError, AttributeError):
                    raise ValueError("Puppeteer v4 physical daemon config is invalid") from None
            if catalog:
                catalog_path = Path(catalog)
                if not catalog_path.is_absolute():
                    catalog_path = robot_path.parent / catalog_path
                referenced.append(catalog_path.resolve().read_bytes())
            if referenced:
                return robot_bytes + b"\0" + b"\0".join(referenced)
        except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
            raise ValueError("Puppeteer v4 robot file or trusted catalog is missing or invalid") from exc
        return robot_bytes

    def _read_v4_robot_files(self, data: dict) -> tuple[tuple[str, ...], tuple[dict, ...]]:
        allowed = {"schema_version", "physical", "robots", "action_journal",
                   "ownership_db", "reachy_neutral_head", "presence_ttl_s", "stop_timeout_s"}
        if set(data) - allowed:
            raise ValueError("unknown Puppeteer v4 room fields")
        if type(data.get("physical", False)) is not bool:
            raise ValueError("Puppeteer v4 physical must be true or false")
        for key, low, high in (("presence_ttl_s", 1, 3600), ("stop_timeout_s", .1, 60)):
            value = data.get(key, 30 if key == "presence_ttl_s" else 5)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"invalid Puppeteer v4 {key}")
        refs = data.get("robots")
        if not isinstance(refs, list) or not refs or len(refs) > 3:
            raise ValueError("Puppeteer v4 requires one to three robot file references")
        kinds, ids, parsed = [], [], []
        for ref in refs:
            if (not isinstance(ref, dict) or set(ref) != {"kind", "id", "file"}
                or ref.get("kind") not in {"reachy", "stackchan", "soarm101"}
                or not isinstance(ref.get("id"), str) or not ref["id"]
                or not isinstance(ref.get("file"), str) or not ref["file"]):
                raise ValueError("invalid Puppeteer v4 robot reference")
            kinds.append(ref["kind"]); ids.append(ref["id"])
            path = Path(ref["file"])
            path = path if path.is_absolute() else self.config_path.parent / path
            try:
                robot = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise ValueError("Puppeteer v4 robot file is missing or invalid") from exc
            if (not isinstance(robot, dict) or set(robot) - {"options"} != {"kind", "id", "provider"}
                or robot["kind"] != ref["kind"] or robot["id"] != ref["id"]
                or not isinstance(robot["provider"], dict)):
                raise ValueError("Puppeteer v4 robot file identity mismatch")
            if ref["kind"] == "soarm101" and robot.get("options"):
                raise ValueError("Puppeteer v4 SO-ARM101 takes no options")
            if ref["kind"] != "soarm101":
                self._check_v4_options(ref["kind"], robot.get("options", {}))
            cfg = robot["provider"]
            if ref["kind"] in {"reachy", "stackchan"}:
                provider_fields = {
                    "backend", "device", "instance_id", "name", "allowed_tools", "assets",
                    "asset_root", "manifest", "action_journal", "ownership_db",
                    "simulation_only", "broker_url",
                    "allow_openshell_host", "broker_token_env", "broker_command",
                    "broker_args", "broker_env", "port", "sdk_version", "host",
                    "connection_mode", "daemon_port", "daemon",
                    "expected_daemon_hardware_id", "restricted_actions", "approved_moves",
                }
                if set(cfg) - provider_fields:
                    raise ValueError("unknown Puppeteer v4 SDK provider fields")
                expected = ("reachy_sdk" if ref["kind"] == "reachy" else "stackchan_sdk")
                if cfg.get("backend") != expected or cfg.get("device") != ref["kind"] or cfg.get("instance_id") != ref["id"]:
                    raise ValueError("Puppeteer v4 SDK provider identity mismatch")
                grants = cfg.get("allowed_tools")
                required = ({"head_pose", "play_move", "device_stop"} if ref["kind"] == "reachy"
                            else {"head_move", "face_update", "device_stop"})
                if not isinstance(grants, list) or not required <= set(grants):
                    raise ValueError("Puppeteer v4 SDK core grants are required")
                if data.get("physical") is not True:
                    raise ValueError("Puppeteer v4 Reachy and StackChan need physical: true on the room")
                self._check_v4_grants(ref["kind"], robot.get("options", {}), grants)
            else:
                # v4's file-composed Puppeteer SO-ARM provider is a broker
                # client binding. It intentionally carries no serial/device
                # settings or credentials, and keeps performer IDs identical.
                if "broker_url" in cfg:
                    broker_fields = {"instance_id", "broker_url", "token_env",
                        "trusted_host_token_env", "choreography_revisions",
                        "choreography_durations_s", "allowed_tools", "enable_joint_jog",
                        "enable_cartesian_jog", "mode", "simulation_state_dir",
                        "simulation_port", "sdk_simulation_executable",
                        "trusted_choreography_catalog", "physical_daemon_config",
                        "sdk_physical_executable", "physical_startup_timeout_s",
                        "startup_target_revision", "pose_targets", "sleep_target", "sleep_check"}
                    if (set(cfg) - broker_fields or cfg.get("instance_id") != ref["id"]
                        or not isinstance(cfg.get("broker_url"), str)
                        or not isinstance(cfg.get("token_env"), str)
                        or not isinstance(cfg.get("allowed_tools"), list)
                        or not {"robot_capabilities", "robot_state", "stop"} <= set(cfg["allowed_tools"])):
                        raise ValueError("Puppeteer v4 SO-ARM broker provider identity or schema mismatch")
                    # Mirror SOARMBrokerConfig's safety-relevant binding rules
                    # here without importing Puppeteer into Conversationalist.
                    mode = cfg.get("mode")
                    if mode not in {"existing_broker", "sdk_simulation_daemon", "sdk_physical_daemon"}:
                        raise ValueError("Puppeteer v4 SO-ARM broker mode is invalid")
                    env_name = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
                    if (not env_name.fullmatch(cfg["token_env"])
                        or (cfg.get("trusted_host_token_env") is not None
                            and not env_name.fullmatch(cfg["trusted_host_token_env"]))):
                        raise ValueError("Puppeteer v4 SO-ARM token environment binding is invalid")
                    parsed_url = urlsplit(cfg["broker_url"])
                    if (parsed_url.scheme != "http"
                        or parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}
                        or parsed_url.port is None or parsed_url.username is not None
                        or parsed_url.password is not None or parsed_url.path not in {"", "/"}
                        or parsed_url.query or parsed_url.fragment):
                        raise ValueError("Puppeteer v4 SO-ARM broker URL must use explicit loopback HTTP")
                    allowed_tools = cfg["allowed_tools"]
                    known_tools = {"robot_health", "robot_capabilities", "robot_state", "list_assets",
                        "replay_recording", "replay_sequence", "capture_camera", "go_pose",
                        "jog_joint", "jog_cartesian", "move_gripper", "sleep", "sleep_up", "stop"}
                    if (len(set(allowed_tools)) != len(allowed_tools)
                        or set(allowed_tools) - known_tools
                        or cfg.get("enable_joint_jog", False) != ("jog_joint" in allowed_tools)
                        or cfg.get("enable_cartesian_jog", False) != ("jog_cartesian" in allowed_tools)):
                        raise ValueError("Puppeteer v4 SO-ARM broker tool profile is invalid")
                    revisions = cfg.get("choreography_revisions", {})
                    pose_targets = cfg.get("pose_targets", {})
                    if (not isinstance(pose_targets, dict) or len(pose_targets) > 25
                            or (pose_targets and not cfg.get("trusted_host_token_env"))
                            or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
                                or name in {"idle", "listening", "speaking", "wake_to_neutral"}
                                or name in revisions or not isinstance(pin, str)
                                or not re.fullmatch(r"sha256:[0-9a-f]{64}", pin)
                                for name, pin in pose_targets.items())):
                        raise ValueError("Puppeteer v4 SO-ARM pose target pins are invalid")
                    durations = cfg.get("choreography_durations_s", {})
                    if (not isinstance(revisions, dict) or not isinstance(durations, dict)
                        or any(not isinstance(name, str) for name in revisions)
                        or (revisions and (not cfg.get("trusted_host_token_env")
                            or not {"idle", "speaking"} <= set(revisions)
                            or set(revisions) - {"idle", "listening", "speaking", "yes", "no", "acknowledge", "neutral", "confused", "surprised", "curious"}
                            or set(durations) != set(revisions)
                            or any(not isinstance(pin, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", pin)
                                   for pin in revisions.values())
                            or any(type(duration) not in (int, float) or not math.isfinite(duration)
                                   or not 0.2 <= duration <= 30 for duration in durations.values()))
                        or (not revisions and durations))):
                        raise ValueError("Puppeteer v4 SO-ARM choreography pins or durations are invalid")
                    target = cfg.get("startup_target_revision")
                    if target is not None and (not isinstance(target, str)
                            or not re.fullmatch(r"sha256:[0-9a-f]{64}", target) or not revisions):
                        raise ValueError("Puppeteer v4 startup target pin is invalid")
                    if mode == "sdk_physical_daemon" and revisions and not target:
                        raise ValueError("Puppeteer v4 physical choreography requires a pinned startup target")
                    if mode == "sdk_physical_daemon" and data.get("physical") is not True:
                        raise ValueError("Puppeteer v4 SDK physical daemon needs physical: true on the room")
                    sleep_target = cfg.get("sleep_target")
                    if sleep_target is not None and sleep_target not in pose_targets:
                        raise ValueError("Puppeteer v4 sleep_target must name a pose target")
                    if type(cfg.get("sleep_check", True)) is not bool:
                        raise ValueError("Puppeteer v4 sleep_check must be true or false")
                    if mode == "sdk_simulation_daemon":
                        state_dir = cfg.get("simulation_state_dir")
                        port = cfg.get("simulation_port", 8765)
                        if (not isinstance(state_dir, str) or not Path(state_dir).is_absolute()
                            or type(port) is not int or not 1 <= port <= 65535
                            or cfg["broker_url"] != f"http://127.0.0.1:{port}"):
                            raise ValueError("Puppeteer v4 SO-ARM simulation binding is invalid")
                        catalog = cfg.get("trusted_choreography_catalog")
                        if revisions:
                            if not isinstance(catalog, str) or not catalog:
                                raise ValueError("Puppeteer v4 SO-ARM trusted catalog is required")
                            catalog_path = Path(catalog)
                            if not catalog_path.is_absolute():
                                catalog_path = path.resolve().parent / catalog_path
                            if not catalog_path.is_file():
                                raise ValueError("Puppeteer v4 SO-ARM trusted catalog is missing")
                    elif ((cfg.get("simulation_state_dir") is not None
                           or cfg.get("trusted_choreography_catalog") is not None)
                          and mode != "sdk_physical_daemon"):
                        raise ValueError("Puppeteer v4 SO-ARM simulation fields require SDK-owned mode")
                    if mode == "sdk_physical_daemon":
                        daemon_config = cfg.get("physical_daemon_config")
                        startup_timeout = cfg.get("physical_startup_timeout_s", 10)
                        if (not isinstance(daemon_config, str) or not Path(daemon_config).is_absolute()
                            or type(startup_timeout) not in (int, float) or not 1 <= startup_timeout <= 60
                            or cfg["broker_url"] != f"http://127.0.0.1:{parsed_url.port}"):
                            raise ValueError("Puppeteer v4 SO-ARM physical daemon binding is invalid")
                        try:
                            physical = json.loads(Path(daemon_config).read_bytes())
                            required_physical = {
                                "physical", "robot_id", "port", "calibration_id", "lease_path",
                                "trusted_catalog_path", "loopback_port", "state_dir", "operator_choreography_only"}
                            if (not isinstance(physical, dict) or not required_physical <= set(physical)
                                or set(physical) - required_physical - {"establish_hold_on_startup", "joint_speed_deg_s", "joint_acceleration_deg_s2"}
                                or physical.get("physical") is not True
                                or type(physical.get("operator_choreography_only")) is not bool
                                or type(physical.get("establish_hold_on_startup", False)) is not bool
                                or (physical.get("establish_hold_on_startup") and not physical.get("operator_choreography_only"))
                                or physical.get("loopback_port") != parsed_url.port
                                or not all(isinstance(physical.get(k), str) and Path(physical[k]).is_absolute()
                                           for k in (("trusted_catalog_path", "state_dir") if physical.get("operator_choreography_only")
                                                     else ("lease_path", "trusted_catalog_path", "state_dir")))):
                                raise ValueError
                            configured_catalog = cfg.get("trusted_choreography_catalog")
                            if configured_catalog:
                                configured_catalog_path = Path(configured_catalog)
                                if not configured_catalog_path.is_absolute():
                                    configured_catalog_path = path.resolve().parent / configured_catalog_path
                                if configured_catalog_path.resolve() != Path(physical["trusted_catalog_path"]).resolve():
                                    raise ValueError
                        except (OSError, ValueError, TypeError, KeyError):
                            raise ValueError("Puppeteer v4 SO-ARM physical daemon config is missing or mismatched") from None
                    elif cfg.get("physical_daemon_config") is not None:
                        raise ValueError("physical daemon config requires sdk_physical_daemon mode")
                    parsed.append(robot)
                    continue
                raise ValueError("Puppeteer v4 SO-ARM101 needs the SDK broker binding (broker_url)")
            parsed.append(robot)
        if len(set(kinds)) != len(kinds) or len(set(ids)) != len(ids):
            raise ValueError("Puppeteer v4 robot kinds and performer IDs must be unique")
        for key in ("action_journal", "ownership_db"):
            value = data.get(key)
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError(f"Puppeteer v4 requires absolute {key}")
        if Path(data["action_journal"]).resolve() == Path(data["ownership_db"]).resolve():
            raise ValueError("Puppeteer v4 action journal and ownership state must be distinct")
        if "reachy" in kinds:
            neutral = data.get("reachy_neutral_head")
            if not isinstance(neutral, str) or not Path(neutral).is_absolute():
                raise ValueError("Puppeteer v4 Reachy requires absolute reachy_neutral_head")
        elif "reachy_neutral_head" in data:
            raise ValueError("Puppeteer v4 neutral pose requires selected Reachy")
        return tuple(ids), tuple(parsed)

    def preflight(self) -> dict[str, object]:
        """No SDK import, device connection, motion, or credential disclosure.

        An operator-readable *configuration* check, never evidence that the
        connected hardware has the expected firmware or is settled.
        """
        self.require_unchanged()
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        self._read_v4_robot_files(data)
        soarm_motion = {}
        for ref in data["robots"]:
            if ref["kind"] == "soarm101":
                path = Path(ref["file"])
                if not path.is_absolute():
                    path = self.config_path.parent / path
                provider = json.loads(path.read_text(encoding="utf-8"))["provider"]
                revisions = provider.get("choreography_revisions", {})
                soarm_motion[ref["id"]] = {
                    "configuration": "motion_configured" if revisions else "hold_only",
                    "configured_behaviors": sorted(revisions),
                    "live_readiness_verified": False,
                }
        if "reachy_neutral_head" in data:
            try:
                matrix = json.loads(Path(data["reachy_neutral_head"]).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise ValueError("operator-reviewed Reachy neutral file is invalid") from exc
            if (not isinstance(matrix, list) or len(matrix) != 4 or
                any(not isinstance(row, list) or len(row) != 4 or
                    any(type(v) not in (int, float) or not math.isfinite(v) for v in row)
                    for row in matrix) or matrix[3] != [0, 0, 0, 1]):
                raise ValueError("Reachy neutral must be a finite affine 4x4 matrix")
            if any(profile.get("idle") == "original_v1" for profile in self.motion_profiles.values()) and any(
                    abs(value - (1.0 if i == j else 0.0)) > 1e-9
                    for i, row in enumerate(matrix) for j, value in enumerate(row)):
                raise ValueError("original_v1 requires an operator-reviewed identity neutral reference")
        return {
            "schema": "forge.ensemble_preflight.v1", "outcome": "static_config_valid",
            "config_sha256": self.digest, "schema_version": 4,
            "performer_ids": list(self.performer_ids), "motion_profiles": {
                name: {"idle": profile.get("idle", "legacy"), "speaking": profile.get("speaking", "legacy")}
                for name, profile in self.motion_profiles.items()},
            "soarm_motion": soarm_motion,
            "speech_driven_performers": list(self.speech_driven_performers),
            "sdk_connections_opened": False,
            "physical_motion_dispatched": False, "hardware_capabilities_verified": False,
            "physical_neutral_verified": False, "physical_stop_verified": False,
            "physical_sleep_verified": False, "acoustic_playout_measured": False,
        }


class EnsembleSession:
    """MCP stdio lifecycle and serialized, non-retrying semantic room events."""

    def __init__(
        self, config: PuppeteerEnsembleConfig, *, room_id: str, caller_id: str,
        outgoing_speech_envelope: bool = False,
        native_gain_performer: str | None = None,
        park_on_close: bool = True,
    ):
        if not room_id or not caller_id:
            raise ValueError("authorized LiveKit room and caller are required for physical ensemble")
        if native_gain_performer is not None and native_gain_performer not in config.performer_ids:
            raise ValueError("native gain performer must be selected in the operator config")
        self.config = config
        self.room_id = room_id
        self.caller_id = caller_id
        self._sequence = -1
        self._last_state: str | None = None
        # Startup idle is emitted once for this host session, after Puppeteer
        # readiness is verified. Reopening the child must not rearm motion.
        self._initial_idle_sent = False
        self._last_source: Literal["agent_state", "playback"] = "agent_state"
        self._queue: asyncio.Queue[tuple[str, dict, asyncio.Future | None]] = asyncio.Queue(128)
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._closed = False
        self._outgoing_speech_envelope = outgoing_speech_envelope
        self._speech_capable = False
        # Optional Puppeteer tools; absent tools simply disable these paths.
        self._park_on_close = park_on_close
        self._park_capable = False
        self._gate_capable = False
        self._park_done = asyncio.Event()
        self._park_summary: dict[str, object] | None = None
        self._native_gain_performer = native_gain_performer
        self._native_gain_capable = False
        self._native_pacer: NativeSpeechPacer | None = None
        self._native_stream: str | None = None
        self._native_pending: dict[str, NativeSpeechPacer] = {}
        self._native_streams_seen: set[str] = set()
        self._native_armed = False
        self._native_speaking_ack = False
        self._speech_offers = 0
        self._active_speech_streams: dict[str, str] = {}
        self._cancelled_speech_streams: set[str] = set()
        self._motion_fenced = False
        self._shutdown_result: dict[str, object] | None = None
        self._requires_operator_reconciliation = False
        self._close_lock = asyncio.Lock()
        # One optional envelope stream per performer. A performer's stream that
        # was dropped for backpressure stays dropped; it must never displace a
        # different performer's stream or the automatic presence sequence.
        self._speech_dropped_performers: set[str] = set()
        self._speech_offers_by_performer: dict[str, int] = {}
        # Exactly one optional envelope batch may be in flight on the shared
        # runtime; it names the performer holding it.
        self._envelope_in_flight: str | None = None

    def _presence(self, state: str, *, source: Literal["agent_state", "playback"] = "agent_state",
                  interrupted: bool = False) -> dict:
        self._sequence += 1
        return {
            "session_id": self.room_id,
            "event_id": f"presence-{uuid.uuid4().hex}",
            "sequence": self._sequence,
            "state": state,
            "source": source,
            "interrupted": interrupted,
        }

    def _enqueue(self, tool: str, args: dict, future: asyncio.Future | None = None) -> None:
        try:
            self._queue.put_nowait((tool, args, future))
        except asyncio.QueueFull:
            # No silent dropping or reordering of physical presence. The room
            # becomes unavailable; Puppeteer's own TTL fences stale activity.
            self._closed = True
            logger.error("Puppeteer ensemble queue overflow; this room is disabled")
            if future is not None and not future.done():
                future.set_exception(RuntimeError("Puppeteer ensemble queue overflow"))

    def _enqueue_priority(
        self, tool: str, args: dict, future: asyncio.Future | None = None,
        *, interrupted: bool = False,
    ) -> None:
        """Urgent STOP/room transition overtakes queued optional features.

        No private Queue internals, no second transport. Old semantic presence
        is superseded before dispatch. Queued gestures on a true barge-in get
        an explicit not-dispatched result; admitted actions still require
        Puppeteer's STOP path and cannot be silently retried.
        """
        pending: list[tuple[str, dict, asyncio.Future | None]] = []
        urgent_interrupts: list[tuple[str, dict, asyncio.Future | None]] = []
        while True:
            try:
                queued_tool, queued_args, queued_future = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if tool == "_close" and queued_future is not None:
                if not queued_future.done():
                    queued_future.set_result({
                        "outcome": "unknown", "reason": "room_shutdown",
                        "retry_safe": False,
                    })
                continue
            if queued_tool == "ensemble_speech_envelope":
                for frame in queued_args.get("frames", ()):
                    if isinstance(frame, dict) and isinstance(frame.get("stream_id"), str):
                        self._cancelled_speech_streams.add(frame["stream_id"])
                continue
            if queued_tool == "ensemble_presence":
                if (
                    queued_args.get("interrupted") is True
                    and not interrupted and tool != "_close"
                ):
                    # The next normal idle/listening callback must not erase
                    # an unacknowledged barge-in STOP. Preserve the urgent
                    # interruption before the newer state.
                    urgent_interrupts.append(
                        (queued_tool, queued_args, queued_future),
                    )
                continue  # older nonurgent presence is superseded
            if queued_tool == "ensemble_expression" and interrupted:
                if queued_future is not None and not queued_future.done():
                    queued_future.set_result({
                        "outcome": "denied", "reason": "interrupted_before_dispatch",
                        "retry_safe": False,
                    })
                continue
            pending.append((queued_tool, queued_args, queued_future))
        for item in urgent_interrupts:
            self._queue.put_nowait(item)
        self._enqueue(tool, args, future)
        for item in pending:
            self._queue.put_nowait(item)

    def observe(
        self,
        state: Literal["idle", "listening", "speaking"],
        *,
        source: Literal["agent_state", "playback"] = "agent_state",
        interrupted: bool = False,
    ) -> None:
        """Nonblocking semantic presence, with explicitly attributed timing."""
        if state not in STATES or source not in {"agent_state", "playback"}:
            raise ValueError("invalid ensemble presence or timing source")
        if (
            self._closed or self._motion_fenced or self._task is None
            or self._task.done()
        ):
            return
        if interrupted or state != "speaking":
            # Invalidate already offered features from the superseded TTS
            # utterance, including batches that raced with a barge-in.
            self._cancelled_speech_streams.update(self._active_speech_streams.values())
            self._active_speech_streams.clear()
            self._native_speaking_ack = False
            if interrupted or (self._native_armed and source != "playback"):
                for pacer in self._native_pending.values():
                    pacer.invalidate()
                self._native_pending.clear()
                self._native_armed = False
            elif self._native_armed:
                # A normally finished output segment is not an interruption
                # of a later segment that TTS has already generated.
                if self._native_pacer is not None:
                    self._native_pacer.invalidate()
                if self._native_stream is not None:
                    self._native_pending.pop(self._native_stream, None)
                self._native_armed = False
        self._last_state = state
        self._last_source = source
        args = self._presence(state, source=source, interrupted=interrupted)
        if interrupted or state != "speaking":
            self._enqueue_priority(
                "ensemble_presence", args, interrupted=interrupted,
            )
        else:
            self._enqueue("ensemble_presence", args)
        logger.info(
            "puppeteer_presence_enqueued",
            extra={
                **_presence_timing_fields(args),
                "timing_source": "conversationalist_mcp_queue",
                "enqueued_at_unix_ns": time.time_ns(),
                "enqueued_at_monotonic_ns": time.monotonic_ns(),
            },
        )

    def begin_native_gain(self, stream_id: str) -> bool:
        """Prepare one stream; this does not associate it with playback."""
        if (not self._native_gain_capable or self._closed or self._motion_fenced
                or self._task is None or self._task.done()
                or not isinstance(stream_id, str) or not 1 <= len(stream_id) <= 128
                or stream_id in self._native_streams_seen
                or len(self._native_streams_seen) >= 4096
                or len(self._native_pending) >= 4):
            return False
        pacer = NativeSpeechPacer(drop_late=True)
        pacer.bind(stream_id)
        self._native_pending[stream_id] = pacer
        if self._native_pacer is None:
            self._native_pacer = pacer
            self._native_stream = stream_id
        self._native_streams_seen.add(stream_id)
        return True

    def offer_native_gain(self, frames: list[dict[str, object]]) -> bool:
        """Buffer content-free features; no transport or hardware call occurs."""
        if (not isinstance(frames, list) or not frames or not isinstance(frames[0], dict)
                or self._closed or self._motion_fenced):
            return False
        stream_id = frames[0].get("stream_id")
        if not isinstance(stream_id, str):
            return False
        pacer = self._native_pending.get(stream_id)
        if pacer is None:
            return False
        try:
            accepted = pacer.offer(frames)
            if not accepted and pacer is self._native_pacer:
                self._native_armed = False
            return accepted
        except (TypeError, ValueError):
            pacer.invalidate()
            if pacer is self._native_pacer:
                self._native_armed = False
            return False

    def cancel_native_gain(self, stream_id: str) -> None:
        """Discard one feature stream without retrying or changing voice."""
        if not isinstance(stream_id, str):
            return
        pacer = self._native_pending.pop(stream_id, None)
        if pacer is not None:
            pacer.invalidate()
            if pacer is self._native_pacer:
                self._native_armed = False

    def associate_native_playback(self, stream_id: str, *, output_started_at: float) -> bool:
        """Bind a verified stream to an output segment's monotonic start time.

        The caller must prove stream/segment identity. LiveKit output events
        alone do not carry that identity, and acoustic playout is not measured.
        """
        if not isinstance(stream_id, str):
            return False
        pacer = self._native_pending.get(stream_id)
        if (pacer is None
                or self._closed or self._motion_fenced or self._last_state != "speaking"
                or self._last_source != "playback"):
            return False
        try:
            pacer.arm(stream_id=stream_id, output_started_at=output_started_at)
            if self._native_pacer is not None and self._native_pacer is not pacer:
                self._native_pacer.invalidate()
                self._native_pending.pop(self._native_stream, None)
            self._native_pacer = pacer
            self._native_stream = stream_id
            self._native_armed = True
            self._queue.put_nowait(("_native_wakeup", {}, None))
            return True
        except (ValueError, asyncio.QueueFull):
            pacer.invalidate()
            if pacer is self._native_pacer:
                self._native_armed = False
            return False


    def offer_speech_envelope(
        self, frames: list[dict[str, object]], *, performer_id: str | None = None,
    ) -> bool:
        """Best-effort feature-only path; it NEVER blocks or disables a voice room.

        Admission merely enqueues generated-PCM observations to the same
        room-owned MCP child. A failed batch disables observation for that
        synthesized stream; no skipped sequence is ever silently repaired.

        Gating decision for the two-performer case: the shared 300 ms runtime is
        serialized by one queue, so Reachy's 50 ms and StackChan's 2 s speech
        targets contend rather than interleave. An optional envelope batch may
        therefore only enter when the queue is empty enough to guarantee the
        next urgent presence event keeps its slot. Each performer holds at most
        one pending envelope batch, so a slower performer cannot starve a
        faster one or delay STOP. A performer dropped for backpressure stays
        dropped for the rest of the room; it is never silently repaired or
        retried. ``performer_id`` attributes the stream for that gate and for
        honest logging; ``None`` means an unattributed stream that shares the
        single "unattributed" slot.
        """
        if (
            not self._outgoing_speech_envelope or not self._speech_capable
            or self._closed or self._motion_fenced
            or self._task is None or self._task.done()
            or not isinstance(frames, list) or not 1 <= len(frames) <= 8
        ):
            return False
        stream_id = frames[0].get("stream_id") if isinstance(frames[0], dict) else None
        if not isinstance(stream_id, str) or not stream_id or stream_id in self._cancelled_speech_streams:
            return False
        slot = performer_id if isinstance(performer_id, str) and performer_id else "unattributed"
        if slot != "unattributed" and slot not in self.config.performer_ids:
            return False
        if slot in self._speech_dropped_performers:
            return False
        if any(
            not isinstance(frame, dict)
            or frame.get("stream_id") != stream_id
            for frame in frames
        ):
            return False
        previous_stream = self._active_speech_streams.get(slot)
        if previous_stream is not None and previous_stream != stream_id:
            # New utterance supersedes a previous one; the source must start
            # from sequence zero, never silently continue a changed stream.
            if frames[0].get("sequence") != 0:
                return False
            self._cancelled_speech_streams.add(previous_stream)
        pending = sum(
            1 for entry in list(self._queue._queue)
            if entry[0] == "ensemble_speech_envelope"
            and entry[1].get("performer_id") == slot
        )
        if pending >= SPEECH_ENVELOPE_MAX_QUEUE:
            self._speech_dropped_performers.add(slot)
            logger.warning(
                "Optional speech envelope dropped under MCP queue backpressure",
                extra={"performer_id": slot},
            )
            return False
        try:
            self._queue.put_nowait((
                "ensemble_speech_envelope",
                {
                    "session_id": self.room_id,
                    "performer_id": slot,
                    "frames": frames,
                },
                None,
            ))
            self._active_speech_streams[slot] = stream_id
            self._speech_offers += 1
            offers = self._speech_offers_by_performer.get(slot, 0) + 1
            self._speech_offers_by_performer[slot] = offers
            if offers == 1:
                # One line per performer proves the opt-in path is live
                # without per-hop noise.
                logger.info(
                    "Outgoing speech envelope reached the robot room",
                    extra={"performer_id": slot},
                )
        except asyncio.QueueFull:
            self._speech_dropped_performers.add(slot)
            return False
        return True

    async def start(self) -> dict:
        if self._task is not None:
            raise RuntimeError("ensemble room already started")
        if self._requires_operator_reconciliation:
            raise RuntimeError("prior Puppeteer STOP unresolved; operator reconciliation required")
        self.config.require_unchanged()
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._task = asyncio.create_task(self._serve(), name="puppeteer-ensemble-room")
        try:
            return await asyncio.wait_for(self._ready, timeout=20)
        except BaseException:
            self._closed = True
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            raise

    @staticmethod
    async def _call(session, tool: str, args: dict) -> dict:
        response = await session.call_tool(tool, args)
        if response.is_error or not isinstance(response.structured_content, dict):
            raise RuntimeError("Puppeteer ensemble MCP result is unconfirmed")
        return response.structured_content

    async def _park_before_disconnect(self, session) -> None:
        """End the session at the robots' sleep poses, once, and record what happened.

        Runs *before* the disconnect: Puppeteer lets the running behaviors finish
        (a disconnect is an urgent STOP, which would leave the SO-ARM's SDK session
        unable to take a sleep move) and then moves the robots to sleep. The
        disconnect always follows. A fault stops the park and is reported; it is
        never retried here.
        """
        try:
            if not self._park_capable:
                self._park_summary = {"outcome": "not_attempted"}
                return
            result = await asyncio.wait_for(
                self._call(session, "ensemble_park", {
                    "action_id": f"park-{uuid.uuid4().hex}", "settle_session": True}),
                timeout=PARK_TIMEOUT_S,
            )
            details = result.get("results")
            self._park_summary = {
                "outcome": str(result.get("outcome", "unknown")),
                "performers": {
                    name: str(item.get("outcome", "unknown"))
                    for name, item in (details.items() if isinstance(details, dict) else ())
                    if isinstance(item, dict)
                },
            }
        except Exception:  # noqa: BLE001 - the park outcome is simply unknown
            logger.warning("Puppeteer park outcome unconfirmed")
            self._park_summary = {"outcome": "unknown", "reason": "park_unconfirmed"}
        finally:
            self._park_done.set()

    def _verify_startup_state(self, status: dict[str, object]) -> None:
        """Fail before any room presence when SDK/Puppeteer catalog differs."""
        performers = status.get("performers")
        if (
            status.get("schema") != SCHEMA
            or status.get("session_id") is not None
            or status.get("state") != "disconnected"
            or not isinstance(performers, dict)
            or set(performers) != set(self.config.performer_ids)
        ):
            raise RuntimeError("Puppeteer returned unexpected room or performer identities")
        for detail in performers.values():
            if not isinstance(detail, dict):
                raise TypeError("Puppeteer performer capability details unavailable")
            # Existing synthetic servers can be deliberately minimal; any
            # advertised revision/behavior list must still be well-formed.
            revision = detail.get("behavior_revision")
            if (
                not isinstance(revision, str) or not revision.startswith("sha256:")
                or len(revision) != 71
                or any(c not in "0123456789abcdef" for c in revision[7:])
            ):
                raise RuntimeError("Puppeteer performer revision invalid")
            expressions = detail.get("expressions")
            if not isinstance(expressions, list) or any(
                not isinstance(item, str) for item in expressions
            ):
                raise RuntimeError("Puppeteer expression catalog unavailable")
            states = detail.get("presence_behaviors")
            if (
                not isinstance(states, list)
                or any(item not in STATES for item in states)
                or len(states) != len(set(states))
            ):
                raise RuntimeError("Puppeteer presence catalog invalid")
            kinds = detail.get("expression_kinds")
            if kinds is not None and (
                not isinstance(kinds, dict)
                or set(kinds) - set(expressions)
                or any(value not in {"recorded", "authored"} for value in kinds.values())
            ):
                raise RuntimeError("Puppeteer expression source catalog invalid")
            if detail.get("status") != "idle":
                raise RuntimeError("Puppeteer performer is not ready at room startup")
        logger.info(
            "Puppeteer native ensemble software readiness verified",
            extra={
                "session_id": self.room_id, "performers": sorted(performers),
                "config_sha256": self.config.digest,
                "physical_neutral_verified": False,
                "physical_settling_verified": False,
            },
        )

    async def _serve(self) -> None:
        phase = "import"
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            # Do not forward OpenRouter/LiveKit/Hermes/Platform secrets to robots.
            self.config.require_unchanged()
            params = StdioServerParameters(
                command=self.config.command,
                args=["--config", str(self.config.config_path)],
                env={},
            )
            phase = "launch"
            async with (
                stdio_client(params) as (reader, writer),
                ClientSession(reader, writer) as session,
            ):
                phase = "initialize"
                await session.initialize()
                phase = "verify_contract"
                names = {tool.name for tool in (await session.list_tools()).tools}
                if not TOOLS <= names:
                    raise RuntimeError("Puppeteer room MCP contract unavailable")
                self._speech_capable = (
                    self._outgoing_speech_envelope and "ensemble_speech_envelope" in names
                )
                self._native_gain_capable = (
                    self._native_gain_performer is not None
                    and "ensemble_native_speech_gain" in names
                )
                self._park_capable = self._park_on_close and "ensemble_park" in names
                self._gate_capable = {"ensemble_startup_check", "ensemble_prepare"} <= names
                if self._outgoing_speech_envelope and not self._speech_capable:
                    logger.warning("Puppeteer does not support optional speech envelope; voice continues")
                elif self._speech_capable:
                    logger.info("Puppeteer accepted the optional speech envelope channel")
                status = await self._call(session, "ensemble_state", {})
                self._verify_startup_state(status)
                phase = "room_events"
                if not self._initial_idle_sent:
                    self._last_state = "idle"
                    self._last_source = "agent_state"
                    initial_idle = self._presence("idle", source="agent_state")
                    # Send and confirm this before advertising session
                    # readiness, so a fast LiveKit state callback cannot
                    # supersede the one startup idle event in the queue.
                    try:
                        value = await asyncio.wait_for(
                            self._call(session, "ensemble_presence", initial_idle),
                            timeout=MCP_EVENT_TIMEOUT_S,
                        )
                    except Exception:
                        # Dispatch may have happened even when its receipt was
                        # lost. Keep the same no-rearm quarantine as any other
                        # unconfirmed presence operation.
                        self._motion_fenced = True
                        self._requires_operator_reconciliation = True
                        raise
                    if value.get("outcome") not in {"accepted", "duplicate"}:
                        self._motion_fenced = True
                        self._requires_operator_reconciliation = True
                        raise RuntimeError("Puppeteer rejected initial idle presence")
                    self._initial_idle_sent = True
                if self._ready is not None and not self._ready.done():
                    self._ready.set_result(status)
                next_refresh = time.monotonic() + 10.0
                while True:
                    native_call_pacer = None
                    if (time.monotonic() >= next_refresh and self._queue.empty()
                            and not self._closed and not self._motion_fenced
                            and self._last_state is not None):
                        self._queue.put_nowait((
                            "ensemble_presence",
                            self._presence(self._last_state, source=self._last_source), None,
                        ))
                    if (self._native_armed and self._native_speaking_ack
                            and self._native_pacer is not None and self._queue.empty()):
                        native_call_pacer = self._native_pacer
                        frames = native_call_pacer.take_due(now=time.monotonic())
                        if native_call_pacer.fenced:
                            self._native_armed = False
                            logger.warning("Native speech feature stream expired; observation stopped")
                        if frames:
                            tool = "ensemble_native_speech_gain"
                            args = {"session_id": self.room_id,
                                    "performer_id": self._native_gain_performer, "frames": frames}
                            future = None
                        else:
                            native_call_pacer = None
                    if native_call_pacer is None:
                        try:
                            timeout = 0.02 if self._native_armed else max(
                                0.01, next_refresh - time.monotonic(),
                            )
                            tool, args, future = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                        except TimeoutError:
                            if (time.monotonic() >= next_refresh and not self._closed
                                    and not self._motion_fenced and self._last_state is not None):
                                tool = "ensemble_presence"
                                args = self._presence(self._last_state, source=self._last_source)
                                future = None
                            else:
                                if time.monotonic() >= next_refresh:
                                    next_refresh = time.monotonic() + 10.0
                                continue
                    if tool == "_native_wakeup":
                        continue
                    if tool == "_close":
                        await self._park_before_disconnect(session)
                        try:
                            value = await asyncio.wait_for(
                                self._call(
                                    session, "ensemble_presence",
                                    self._presence("disconnected"),
                                ),
                                timeout=SHUTDOWN_ACK_TIMEOUT_S,
                            )
                            if future is not None and not future.done():
                                future.set_result(value)
                        except Exception:  # noqa: BLE001 - physical STOP may be unknown
                            logger.warning("Puppeteer ensemble disconnect outcome unconfirmed")
                            value = None
                            if future is not None and not future.done():
                                future.set_result({
                                    "outcome": "unknown", "reason": "disconnect_unconfirmed",
                                })
                        # An acknowledged disconnection ends this child; any
                        # queued requests are no longer actionable.
                        while not self._queue.empty():
                            _, _, pending_future = self._queue.get_nowait()
                            if pending_future is not None and not pending_future.done():
                                pending_future.set_result({
                                    "outcome": "unknown", "reason": "room_shutdown",
                                    "retry_safe": False,
                                })
                        break
                    if tool == "ensemble_speech_envelope" and (
                        not self._speech_capable or self._motion_fenced
                        or any(
                            frame.get("stream_id") in self._cancelled_speech_streams
                            for frame in args.get("frames", ()) if isinstance(frame, dict)
                        )
                    ):
                        continue
                    if tool == "ensemble_presence" and self._motion_fenced:
                        continue
                    # Per-performer gate: the slowest target decides the pacing,
                    # so only one envelope batch per performer may be in flight
                    # on the shared runtime. Reachy's 50 ms and StackChan's 2 s
                    # streams are serialized, never merged into one call.
                    envelope_performer = args.get("performer_id") if tool == "ensemble_speech_envelope" else None
                    if isinstance(envelope_performer, str):
                        if self._envelope_in_flight is not None:
                            logger.debug("Envelope batch deferred while another performer's is in flight")
                            self._queue.put_nowait((tool, args, future))
                            await asyncio.sleep(0)
                            continue
                        self._envelope_in_flight = envelope_performer
                    is_presence = tool == "ensemble_presence"
                    if is_presence:
                        call_fields = {
                            **_presence_timing_fields(args),
                            "timing_source": "conversationalist_mcp_call",
                            "call_started_at_unix_ns": time.time_ns(),
                            "call_started_at_monotonic_ns": time.monotonic_ns(),
                        }
                        logger.info("puppeteer_presence_call_started", extra=call_fields)
                    try:
                        value = await asyncio.wait_for(
                            self._call(session, tool, args),
                            timeout=(
                                MCP_ENVELOPE_TIMEOUT_S
                                if tool in {"ensemble_speech_envelope", "ensemble_native_speech_gain"}
                                else MCP_PREPARE_TIMEOUT_S if tool == "ensemble_prepare"
                                else MCP_EVENT_TIMEOUT_S
                            ),
                        )
                        if is_presence:
                            next_refresh = time.monotonic() + 10.0
                            if (value.get("outcome") in {"accepted", "duplicate"}
                                    and args.get("state") == "speaking"
                                    and not args.get("interrupted")
                                    and self._last_state == "speaking"):
                                self._native_speaking_ack = True
                            logger.info(
                                "puppeteer_presence_call_completed",
                                extra={
                                    **call_fields,
                                    "mcp_outcome": value.get("outcome"),
                                    "acknowledged_at_unix_ns": time.time_ns(),
                                    "acknowledged_at_monotonic_ns": time.monotonic_ns(),
                                },
                            )
                        if future is not None and not future.done():
                            future.set_result(value)
                        elif tool == "ensemble_presence" and value.get("outcome") not in {
                            "accepted", "duplicate",
                        }:
                            logger.warning("Puppeteer ensemble presence denied: %s", value.get("reason"))
                            self._motion_fenced = True
                            self._requires_operator_reconciliation = True
                        elif tool == "ensemble_speech_envelope" and value.get("outcome") != "observed":
                            if isinstance(envelope_performer, str):
                                self._speech_dropped_performers.add(envelope_performer)
                            logger.warning("Optional speech envelope observation unconfirmed")
                        elif tool == "ensemble_native_speech_gain" and native_call_pacer is not None:
                            sent = args["frames"]
                            confirmed = (
                                value.get("outcome") == "observed"
                                and type(value.get("frames_accepted")) is int
                                and value["frames_accepted"] == len(sent)
                                and value.get("stream_id") == sent[-1]["stream_id"]
                                and type(value.get("last_sequence")) is int
                                and value["last_sequence"] == sent[-1]["sequence"]
                                and value.get("timing_source") == "generated_pcm"
                                and value.get("physical_motion_dispatched") is False
                            )
                            if not confirmed and native_call_pacer is self._native_pacer:
                                native_call_pacer.invalidate()
                                self._native_armed = False
                                logger.warning("Native speech feature observation unconfirmed")
                            elif confirmed:
                                logger.debug("native_speech_gain_observed", extra={
                                    "session_id": self.room_id,
                                    "performer_id": self._native_gain_performer,
                                    "last_sequence": sent[-1]["sequence"],
                                    "acknowledged_at_monotonic_ns": time.monotonic_ns(),
                                    "timing_source": "generated_pcm",
                                })
                    except Exception as exc:  # noqa: BLE001 - MCP transport exceptions are arbitrary
                        if is_presence:
                            logger.warning(
                                "puppeteer_presence_call_unconfirmed",
                                extra={
                                    **call_fields,
                                    "error_type": type(exc).__name__,
                                    "observed_at_unix_ns": time.time_ns(),
                                    "observed_at_monotonic_ns": time.monotonic_ns(),
                                },
                            )
                        if future is not None and not future.done():
                            future.set_exception(RuntimeError("Puppeteer outcome unconfirmed"))
                        else:
                            logger.warning("Puppeteer ensemble event unconfirmed: %s", type(exc).__name__)
                        if tool == "ensemble_speech_envelope":
                            if isinstance(envelope_performer, str):
                                self._speech_dropped_performers.add(envelope_performer)
                            logger.warning("Optional speech envelope timed out or failed; stream disabled")
                        if (tool == "ensemble_native_speech_gain" and native_call_pacer is not None
                                and native_call_pacer is self._native_pacer):
                            native_call_pacer.invalidate()
                            self._native_armed = False
                            logger.warning("Native speech feature transport failed; stream disabled")
                        if tool in {"ensemble_presence", "ensemble_expression"}:
                            self._motion_fenced = True
                            self._requires_operator_reconciliation = True
                    finally:
                        if isinstance(envelope_performer, str):
                            self._envelope_in_flight = None
        except BaseException as exc:
            for pacer in self._native_pending.values():
                pacer.invalidate()
            self._native_pending.clear()
            self._native_armed = False
            self._park_done.set()
            self._closed = True
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(RuntimeError(f"Puppeteer ensemble startup failed in {phase}"))
            while not self._queue.empty():
                _, _, future = self._queue.get_nowait()
                if future is not None and not future.done():
                    future.set_exception(RuntimeError("Puppeteer ensemble disconnected"))
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.warning("Puppeteer ensemble MCP ended in %s: %s", phase, type(exc).__name__)

    async def _request(self, tool: str, args: dict, *, timeout: float = 15) -> dict:
        if self._closed or self._task is None or self._task.done():
            return {"outcome": "failed", "reason": "ensemble_unavailable", "retry_safe": False}
        if self._motion_fenced and tool not in {"ensemble_state", "ensemble_startup_check"}:
            return {"outcome": "denied", "reason": "operator_reconciliation_required", "retry_safe": False}
        future = asyncio.get_running_loop().create_future()
        self._enqueue(tool, args, future)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except (RuntimeError, TimeoutError):
            return {"outcome": "unknown", "reason": "outcome_unconfirmed", "retry_safe": False}

    async def status(self) -> dict:
        return await self._request("ensemble_state", {})

    async def wait_for_startup(self, timeout: float = STARTUP_READY_TIMEOUT_S) -> bool:
        """Wait until the robots have finished their wake-up motion (or give up).

        The opening greeting should not be spoken over a robot that is still
        rising to neutral. Robots held by the startup question do not count, and
        an older Puppeteer that does not report ``startup_complete`` never delays
        the voice. Returns False if it gave up, in which case the caller carries
        on rather than leaving the user in silence.
        """
        deadline = time.monotonic() + timeout
        while True:
            status = await self._request("ensemble_state", {})
            if status.get("outcome") not in {"observed", "accepted"}:
                return False
            if status.get("startup_complete", True) is True:
                return True
            if time.monotonic() >= deadline:
                logger.warning("Puppeteer startup still running; continuing without waiting")
                return False
            await asyncio.sleep(STARTUP_POLL_S)

    async def startup(self) -> dict:
        """Read-only: is each real robot at its sleep position? Never moves anything."""
        if not self._gate_capable:
            return {"outcome": "unsupported", "reason": "startup_gate_not_available"}
        return await self._request("ensemble_startup_check", {})

    async def prepare(self, performer_id: str, action: str) -> dict:
        """The user's answer to a startup question, for one pinned performer.

        ``move_to_sleep`` runs one sleep move through Puppeteer's normal runtime and
        re-checks; ``confirm_at_sleep`` is for robots that cannot measure their pose.
        Never retried here: a lost receipt is reported as unconfirmed.
        """
        if (not isinstance(performer_id, str) or performer_id not in self.config.performer_ids
                or action not in PREPARE_ACTIONS):
            return {"outcome": "unsupported", "reason": "unknown_performer_or_action"}
        if not self._gate_capable:
            return {"outcome": "unsupported", "reason": "startup_gate_not_available"}
        return await self._request("ensemble_prepare", {
            "performer_id": performer_id, "action": action,
            "action_id": f"sleep-{uuid.uuid4().hex}",
        }, timeout=MCP_PREPARE_TIMEOUT_S + 5)

    async def express(self, name: str, performer_id: str) -> dict:
        """Request one exact catalogued finite expression for one pinned performer.

        This is the SAME serialized MCP room and runtime as automatic
        presence. Discovery is read-only, not permission: Puppeteer and the
        native SDK independently revalidate ownership, assets and authority.
        """
        if (not isinstance(name, str) or not 1 <= len(name) <= 64
                or not isinstance(performer_id, str)
                or performer_id not in self.config.performer_ids):
            return {"outcome": "unsupported", "reason": "unknown_performer_or_expression"}
        if name in {*STATES, "disconnected"}:
            return {"outcome": "unsupported", "reason": "presence_is_automatic"}
        state = await self.status()
        performers = state.get("performers")
        details = performers.get(performer_id) if isinstance(performers, dict) else None
        choices = details.get("expressions") if isinstance(details, dict) else None
        if (not isinstance(choices, list) or name not in choices
                or not all(isinstance(choice, str) for choice in choices)):
            return {"outcome": "unsupported", "reason": "not_in_approved_performer_catalog"}
        if details.get("status") in {"blocked", "unavailable"}:
            return {"outcome": "denied", "reason": "performer_unavailable"}
        return await self._request("ensemble_expression", {
            "expression": name, "action_id": f"gesture-{uuid.uuid4().hex}",
            "performer_ids": [performer_id],
        })

    async def reconnect(self) -> dict:
        """Release and re-open the room-owned MCP child and its SDK sessions.

        Used by the operator's Connect/Disconnect controls: disconnecting frees
        the robots' serial/daemon resources without ending the voice room.
        """
        result = await self.close()
        if self._requires_operator_reconciliation or result.get("outcome") not in {
            "acknowledged", "not_active",
        }:
            raise RuntimeError("Puppeteer STOP or disconnect not confirmed; do not rearm")
        self._task = None
        self._closed = False
        self._ready = None
        self._motion_fenced = False
        # A reopened transport has no fresh host-state observation yet. Do
        # not replay stale idle/listening/speaking into a new device session.
        self._last_state = None
        self._shutdown_result = None
        self._speech_capable = False
        self._park_capable = False
        self._gate_capable = False
        self._park_done.clear()
        self._park_summary = None
        self._native_gain_capable = False
        for pacer in self._native_pending.values():
            pacer.invalidate()
        self._native_pending.clear()
        self._native_pacer = None
        self._native_stream = None
        self._native_armed = False
        self._native_speaking_ack = False
        self._active_speech_streams.clear()
        self._cancelled_speech_streams.clear()
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        return await self.start()

    def shutdown_evidence(self) -> dict[str, object] | None:
        """Latest immutable-by-copy software-only room-close evidence."""
        return dict(self._shutdown_result) if self._shutdown_result is not None else None

    async def close(self) -> dict[str, object]:
        async with self._close_lock:
            return await self._close_once()

    async def _close_once(self) -> dict[str, object]:
        """Bounded room shutdown via Puppeteer's own STOP/ownership lifecycle.

        Never issue an implicit neutral/recording/wake/sleep after an unknown
        action. A room disconnect acknowledgement does not establish physical
        pose, torque state, neutral convergence or motor settling. Puppeteer
        PR #20 remains independently hardware-gated.
        """
        for pacer in self._native_pending.values():
            pacer.invalidate()
        self._native_pending.clear()
        self._native_armed = False
        self._native_speaking_ack = False
        if self._shutdown_result is not None:
            return dict(self._shutdown_result)

        outcome = "not_active" if self._task is None else "unknown"
        per_robot: dict[str, str] = {}
        if self._task is not None and not self._task.done() and not self._closed:
            self._closed = True
            self._motion_fenced = True
            self._cancelled_speech_streams.update(self._active_speech_streams.values())
            future = asyncio.get_running_loop().create_future()
            self._enqueue_priority("_close", {}, future, interrupted=True)
            try:
                response = await asyncio.wait_for(future, timeout=(
                    SHUTDOWN_ACK_TIMEOUT_S + 1 + (PARK_TIMEOUT_S + 3 if self._park_capable else 0)))
                statuses = response.get("performers")
                if isinstance(statuses, dict):
                    per_robot = {
                        name: str(statuses[name].get("status", "unknown"))
                        for name in self.config.performer_ids
                        if isinstance(statuses.get(name), dict)
                    }
                if response.get("outcome") == "accepted" and self._park_capable:
                    try:
                        await asyncio.wait_for(self._park_done.wait(), timeout=PARK_TIMEOUT_S + 3)
                    except TimeoutError:
                        self._park_summary = {"outcome": "unknown", "reason": "park_unconfirmed"}
                if response.get("outcome") == "accepted" and (
                    set(per_robot) == set(self.config.performer_ids)
                    and all(status == "idle" for status in per_robot.values())
                ):
                    outcome = "acknowledged"
                elif response.get("outcome") == "no_session":
                    outcome = "not_active"
                else:
                    outcome = "unknown"
            except (RuntimeError, TimeoutError, asyncio.CancelledError):
                logger.warning("Puppeteer disconnect cannot confirm STOP outcome")
                outcome = "unknown"
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self._task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.gather(self._task, return_exceptions=True), timeout=3,
                )
            except TimeoutError:
                logger.warning("Puppeteer MCP child close still unconfirmed")
                outcome = "unknown"
        if any(status in {"blocked", "unavailable", "unknown"} for status in per_robot.values()):
            outcome = "unknown"
        if outcome == "unknown":
            self._requires_operator_reconciliation = True
        self._shutdown_result = {
            "schema": "forge.ensemble_shutdown.v1",
            "outcome": outcome,
            "performers": per_robot,
            "sdk_stop_command": "puppeteer_owned_disconnect",
            "neutral_request": "not_attempted",
            "sleep_request": (self._park_summary or {"outcome": "not_attempted"})["outcome"],
            "sleep_performers": dict((self._park_summary or {}).get("performers", {})),
            "physical_settling": "unconfirmed",
            "physical_sleep_verified": (self._park_summary or {}).get("outcome") == "parked",
            "retry_safe": False,
        }
        logger.info(
            "puppeteer_room_shutdown_evidence",
            extra={
                "session_id": self.room_id,
                "outcome": outcome,
                "performers": per_robot,
                "physical_settling": "unconfirmed",
            },
        )
        return dict(self._shutdown_result)
