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
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

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
        if (
            data.get("schema_version") not in (1, 2, 3)
            or type(data.get("schema_version")) is not int
            or data.get("motion_authority") != "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION"
        ):
            raise ValueError("Puppeteer ensemble requires explicit operator authorization")
        reachy = data.get("reachy")
        stackchan = data.get("stackchan")
        arm = data.get("soarm101")
        if not isinstance(reachy, dict) or (
            reachy.get("backend") != "reachy_sdk" or reachy.get("device") != "reachy"
        ):
            raise ValueError("Puppeteer ensemble must use native SDK providers")
        if data["schema_version"] in (1, 3):
            if not isinstance(stackchan, dict) or (
                stackchan.get("backend") != "stackchan_sdk"
                or stackchan.get("device") != "stackchan"
            ):
                raise ValueError("Puppeteer ensemble requires an explicit native StackChan")
            if data["schema_version"] == 1:
                if not isinstance(arm, dict) or arm.get("mode") not in {
                    "simulation", "attended_physical_trial",
                }:
                    raise ValueError("three-robot ensemble requires SO-ARM101")
                ids = (
                    reachy.get("instance_id"), stackchan.get("instance_id"),
                    arm.get("instance_id"),
                )
            else:
                if "soarm101" in data:
                    raise ValueError("two-robot ensemble cannot declare SO-ARM101")
                ids = (reachy.get("instance_id"), stackchan.get("instance_id"))
        else:
            if "stackchan" in data or "soarm101" in data:
                raise ValueError("Reachy-only deployment cannot include other devices")
            ids = (reachy.get("instance_id"),)
        if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
            raise ValueError("Puppeteer ensemble must pin unique performer identities")
        for name in ("action_journal", "ownership_db", "reachy_neutral_head"):
            value = data.get(name)
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError(f"Puppeteer ensemble requires absolute {name}")
        if Path(data["action_journal"]).resolve() == Path(data["ownership_db"]).resolve():
            raise ValueError("Puppeteer action journal and ownership state must be distinct")
        # Perform the no-I/O part of PR1's explicit profile admission again
        # at the worker boundary. Actual SDK capability/geometry validation
        # remains authoritative only in the Puppeteer launcher.
        profiles = data.get("motion_profiles", {})
        driven = data.get("speech_driven_performers", [])
        if not isinstance(profiles, dict) or not isinstance(driven, (list, tuple)):
            raise ValueError("motion profiles and speech performers must be explicit mappings")
        if any(not isinstance(x, str) or x not in ids for x in driven) or len(set(driven)) != len(driven):
            raise ValueError("speech-driven performer identities must be unique and selected")
        if set(driven) - {reachy.get("instance_id"), (
            stackchan.get("instance_id") if isinstance(stackchan, dict) else None
        )}:
            raise ValueError("SO-ARM is not approved for speech-reactive streaming")
        reviewed: dict[str, dict[str, str]] = {}
        for performer, choice in profiles.items():
            if performer not in ids or not isinstance(choice, dict) or set(choice) - {"idle", "speaking"}:
                raise ValueError("motion profile references an unsupported performer or field")
            idle = choice.get("idle", "legacy")
            speaking = choice.get("speaking", "legacy")
            if idle not in {"legacy", "native_v1", "smooth_v2"} or speaking not in {
                "legacy", "native_v1", "smooth_v2",
            }:
                raise ValueError("unknown Puppeteer motion profile")
            if performer == (arm.get("instance_id") if isinstance(arm, dict) else None) and (
                idle != "legacy" or speaking != "legacy"
            ):
                raise ValueError("SO-ARM only permits approved finite legacy choreography")
            if performer == reachy.get("instance_id") and (
                idle != "legacy" or speaking != "legacy"
            ):
                grants = reachy.get("allowed_tools", [])
                neutral = data.get("reachy_neutral_antennas_rad")
                sway = data.get("reachy_antenna_sway_rad", 0.18)
                if not isinstance(grants, list) or "pose_and_antennas" not in grants:
                    raise ValueError("native Reachy profiles require pose_and_antennas grant")
                if (
                    not isinstance(neutral, (list, tuple)) or len(neutral) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in neutral)
                    or type(sway) not in (int, float) or not math.isfinite(sway)
                    or sway < 0 or any(abs(v) + sway > math.pi / 2 for v in neutral)
                ):
                    raise ValueError("native Reachy requires bounded reviewed antenna neutral")
            if speaking == "smooth_v2" and performer not in driven:
                raise ValueError("smooth_v2 speaking requires explicit speech-driven performer")
            reviewed[performer] = {"idle": idle, "speaking": speaking}
        object.__setattr__(self, "performer_ids", ids)
        object.__setattr__(self, "schema_version", data["schema_version"])
        object.__setattr__(self, "motion_profiles", reviewed)
        object.__setattr__(self, "speech_driven_performers", tuple(driven))
        object.__setattr__(self, "digest", hashlib.sha256(source).hexdigest())

    def require_unchanged(self) -> None:
        """Fail before starting the child if an approved config changed on disk."""
        if hashlib.sha256(self.config_path.read_bytes()).hexdigest() != self.digest:
            raise PermissionError("Puppeteer ensemble operator config changed since validation")

    def preflight(self) -> dict[str, object]:
        """No SDK import, device connection, motion, or credential disclosure.

        An operator-readable *configuration* check, never evidence that the
        connected hardware has the expected firmware or is settled.
        """
        self.require_unchanged()
        return {
            "schema": "forge.ensemble_preflight.v1",
            "outcome": "static_config_valid",
            "config_sha256": self.digest,
            "schema_version": self.schema_version,
            "performer_ids": list(self.performer_ids),
            "motion_profiles": {
                name: {
                    "idle": self.motion_profiles.get(name, {}).get("idle", "legacy"),
                    "speaking": self.motion_profiles.get(name, {}).get("speaking", "legacy"),
                } for name in self.performer_ids
            },
            "speech_driven_performers": list(self.speech_driven_performers),
            "sdk_connections_opened": False,
            "physical_motion_dispatched": False,
            "hardware_capabilities_verified": False,
            "physical_neutral_verified": False,
            "physical_stop_verified": False,
            "physical_sleep_verified": False,
            "acoustic_playout_measured": False,
        }


class EnsembleSession:
    """MCP stdio lifecycle and serialized, non-retrying semantic room events."""

    def __init__(
        self, config: PuppeteerEnsembleConfig, *, room_id: str, caller_id: str,
        outgoing_speech_envelope: bool = False,
    ):
        if not room_id or not caller_id:
            raise ValueError("authorized LiveKit room and caller are required for physical ensemble")
        self.config = config
        self.room_id = room_id
        self.caller_id = caller_id
        self._sequence = -1
        self._last_state: str | None = None
        self._last_source: Literal["agent_state", "playback"] = "agent_state"
        self._queue: asyncio.Queue[tuple[str, dict, asyncio.Future | None]] = asyncio.Queue(128)
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._closed = False
        self._outgoing_speech_envelope = outgoing_speech_envelope
        self._speech_capable = False
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
        while True:
            try:
                queued_tool, queued_args, queued_future = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if queued_tool == "ensemble_speech_envelope":
                for frame in queued_args.get("frames", ()):
                    if isinstance(frame, dict) and isinstance(frame.get("stream_id"), str):
                        self._cancelled_speech_streams.add(frame["stream_id"])
                continue
            if queued_tool == "ensemble_presence":
                continue  # superseded by a later monotonic sequence
            if queued_tool == "ensemble_expression" and interrupted:
                if queued_future is not None and not queued_future.done():
                    queued_future.set_result({
                        "outcome": "denied", "reason": "interrupted_before_dispatch",
                        "retry_safe": False,
                    })
                continue
            pending.append((queued_tool, queued_args, queued_future))
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
        for name, detail in performers.items():
            if not isinstance(detail, dict):
                raise RuntimeError("Puppeteer performer capability details unavailable")
            # Existing synthetic servers can be deliberately minimal; any
            # advertised revision/behavior list must still be well-formed.
            revision = detail.get("behavior_revision")
            if revision is not None and (
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
            if states is not None and (
                not isinstance(states, list)
                or any(item not in STATES for item in states)
                or len(states) != len(set(states))
            ):
                raise RuntimeError("Puppeteer presence catalog invalid")
            if detail.get("status") in {"blocked", "unavailable"}:
                raise RuntimeError("Puppeteer performer requires operator review")
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
                if self._outgoing_speech_envelope and not self._speech_capable:
                    logger.warning("Puppeteer does not support optional speech envelope; voice continues")
                elif self._speech_capable:
                    logger.info("Puppeteer accepted the optional speech envelope channel")
                status = await self._call(session, "ensemble_state", {})
                self._verify_startup_state(status)
                phase = "room_events"
                if self._ready is not None and not self._ready.done():
                    self._ready.set_result(status)
                while True:
                    try:
                        tool, args, future = await asyncio.wait_for(self._queue.get(), timeout=10)
                    except TimeoutError:
                        if not self._closed and not self._motion_fenced and self._last_state is not None:
                            tool = "ensemble_presence"
                            args = self._presence(self._last_state, source=self._last_source)
                            future = None
                        else:
                            continue
                    if tool == "_close":
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
                            if future is not None and not future.done():
                                future.set_result({
                                    "outcome": "unknown", "reason": "disconnect_unconfirmed",
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
                            timeout=MCP_EVENT_TIMEOUT_S,
                        )
                        if is_presence:
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
                            logger.warning("Optional speech envelope observation unconfirmed")
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
                        if tool in {"ensemble_presence", "ensemble_expression"}:
                            self._motion_fenced = True
                            self._requires_operator_reconciliation = True
                    finally:
                        if isinstance(envelope_performer, str):
                            self._envelope_in_flight = None
        except BaseException as exc:
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

    async def _request(self, tool: str, args: dict) -> dict:
        if self._closed or self._task is None or self._task.done():
            return {"outcome": "failed", "reason": "ensemble_unavailable", "retry_safe": False}
        if self._motion_fenced and tool != "ensemble_state":
            return {"outcome": "denied", "reason": "operator_reconciliation_required", "retry_safe": False}
        future = asyncio.get_running_loop().create_future()
        self._enqueue(tool, args, future)
        try:
            return await asyncio.wait_for(future, timeout=15)
        except (RuntimeError, TimeoutError):
            return {"outcome": "unknown", "reason": "outcome_unconfirmed", "retry_safe": False}

    async def status(self) -> dict:
        return await self._request("ensemble_state", {})

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
        self._shutdown_result = None
        self._speech_capable = False
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
                response = await asyncio.wait_for(future, timeout=SHUTDOWN_ACK_TIMEOUT_S + 1)
                statuses = response.get("performers")
                if isinstance(statuses, dict):
                    per_robot = {
                        name: str(statuses[name].get("status", "unknown"))
                        for name in self.config.performer_ids
                        if isinstance(statuses.get(name), dict)
                    }
                if response.get("outcome") in {"accepted", "no_session"}:
                    outcome = (
                        "acknowledged" if response.get("outcome") == "accepted"
                        else "not_active"
                    )
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
            "sleep_request": "not_attempted",
            "physical_settling": "unconfirmed",
            "physical_sleep_verified": False,
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
