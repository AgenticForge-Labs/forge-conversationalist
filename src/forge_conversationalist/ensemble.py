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
        # All detailed hardware policy, SHA revision, calibration, servo limits,
        # grants and preflight remain strictly enforced by the Puppeteer
        # launcher and SDK at startup and at every native motion dispatch.
        object.__setattr__(self, "performer_ids", ids)
        object.__setattr__(self, "digest", hashlib.sha256(source).hexdigest())

    def require_unchanged(self) -> None:
        """Fail before starting the child if an approved config changed on disk."""
        if hashlib.sha256(self.config_path.read_bytes()).hexdigest() != self.digest:
            raise PermissionError("Puppeteer ensemble operator config changed since validation")


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
        self._speech_drop_stream: str | None = None

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
        if self._closed or self._task is None or self._task.done():
            return
        if interrupted or state != "speaking":
            # A new stream must be explicitly created by the next TTS node.
            # Any already queued feature batch is still preview-only; it can
            # never bypass Puppeteer motion quarantine or trigger a motor.
            self._speech_drop_stream = None
        self._last_state = state
        self._last_source = source
        args = self._presence(state, source=source, interrupted=interrupted)
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


    def offer_speech_envelope(self, frames: list[dict[str, object]]) -> bool:
        """Best-effort feature-only path; it NEVER blocks or disables a voice room.

        Admission merely enqueues generated-PCM observations to the same
        room-owned MCP child. A failed batch disables observation for that
        synthesized stream; no skipped sequence is ever silently repaired.
        """
        if (
            not self._outgoing_speech_envelope or not self._speech_capable
            or self._closed or self._task is None or self._task.done()
            or not isinstance(frames, list) or not 1 <= len(frames) <= 8
        ):
            return False
        stream_id = frames[0].get("stream_id") if isinstance(frames[0], dict) else None
        if not isinstance(stream_id, str) or not stream_id:
            return False
        if self._speech_drop_stream == stream_id:
            return False
        if self._queue.qsize() >= 24:
            self._speech_drop_stream = stream_id
            logger.warning("Optional speech envelope dropped under MCP queue backpressure")
            return False
        if self._speech_drop_stream != stream_id:
            self._speech_drop_stream = None
        try:
            self._queue.put_nowait((
                "ensemble_speech_envelope",
                {"session_id": self.room_id, "frames": frames},
                None,
            ))
        except asyncio.QueueFull:
            self._speech_drop_stream = stream_id
            return False
        return True

    async def start(self) -> dict:
        if self._task is not None:
            raise RuntimeError("ensemble room already started")
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
                status = await self._call(session, "ensemble_state", {})
                if (
                    status.get("schema") != SCHEMA
                    or status.get("session_id") is not None
                    or status.get("state") != "disconnected"
                    or set(status.get("performers", {})) != set(self.config.performer_ids)
                ):
                    raise RuntimeError("Puppeteer returned unexpected room or performer identities")
                phase = "room_events"
                if self._ready is not None and not self._ready.done():
                    self._ready.set_result(status)
                while True:
                    try:
                        tool, args, future = await asyncio.wait_for(self._queue.get(), timeout=10)
                    except TimeoutError:
                        if not self._closed and self._last_state is not None:
                            tool = "ensemble_presence"
                            args = self._presence(self._last_state, source=self._last_source)
                            future = None
                        else:
                            continue
                    if tool == "_close":
                        try:
                            await self._call(session, "ensemble_presence", self._presence("disconnected"))
                        except Exception:  # noqa: BLE001 - never infer physical STOP on MCP error
                            logger.warning("Puppeteer ensemble disconnect outcome unconfirmed")
                        if future is not None and not future.done():
                            future.set_result({})
                        break
                    if tool == "ensemble_speech_envelope" and not self._speech_capable:
                        continue
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
                        value = await self._call(session, tool, args)
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

    async def close(self) -> None:
        if self._task is None:
            return
        if not self._task.done() and not self._closed:
            self._closed = True
            future = asyncio.get_running_loop().create_future()
            self._enqueue("_close", {}, future)
            try:
                await asyncio.wait_for(future, timeout=5)
            except (RuntimeError, TimeoutError):
                logger.warning("Puppeteer ensemble disconnect confirmation unavailable")
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
