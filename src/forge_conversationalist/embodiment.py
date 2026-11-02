"""Optional fake-only Puppeteer embodiment integration for a LiveKit voice room.

The MCP stdio process is owned by one long-running async task. That same
process receives automatic approximate presence observations and optional
explicit expression requests, so they share one coordinator and action journal.
Nothing in this module imports a robot SDK or grants physical motion.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)
SCHEMA = "forge.embodiment.v1"
REQUIRED_TOOLS = frozenset({
    "performer_discover", "conversation_presence",
    "expression_request", "action_lookup",
})


@dataclass(frozen=True)
class EmbodimentConfig:
    """Operator-installed, fake-only Puppeteer MCP launcher configuration."""

    config_path: Path
    performer_id: str
    command: str = "forge-puppeteer-mcp"

    def __post_init__(self) -> None:
        if (not isinstance(self.performer_id, str) or not self.performer_id
                or not self.config_path.is_absolute() or not self.config_path.is_file()):
            raise ValueError("PUPPETEER_EMBODIMENT_CONFIG must be an existing absolute file")
        if self.command != "forge-puppeteer-mcp":
            executable = Path(self.command)
            if (not executable.is_absolute() or executable.name != "forge-puppeteer-mcp"
                    or not executable.is_file() or not os.access(executable, os.X_OK)):
                raise ValueError("PUPPETEER_EMBODIMENT_COMMAND must be a trusted launcher")
        try:
            source = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("PUPPETEER_EMBODIMENT_CONFIG must be valid JSON") from exc
        if not isinstance(source, dict):
            raise TypeError("PUPPETEER_EMBODIMENT_CONFIG must be an object")
        if (source.get("backend") != "fake"
                or source.get("device") not in {"reachy", "stackchan", "soarm101"}
                or source.get("instance_id") != self.performer_id
                or "physical_authority" in source
                or source.get("simulation_only") is True):
            raise ValueError("Embodiment reference integration requires a fake-only profile")
        grants = source.get("allowed_tools")
        if (not isinstance(grants, list)
                or any(not isinstance(name, str) for name in grants)
                or set(grants) != REQUIRED_TOOLS | {"device_info"}
                or len(grants) != len(set(grants))):
            raise ValueError("Embodiment reference requires exact fake-only tool grants")


class EmbodimentSession:
    """Serial MCP client for approximate conversation signals and explicit cues.

    Owns a single stdio child for the room lifetime, isolates robot methods
    from the voice app, and does not retry ambiguous tool submissions.
    """

    def __init__(
        self, config: EmbodimentConfig, *, room_id: str, caller_id: str
    ) -> None:
        if not room_id or not caller_id:
            raise ValueError("stable room and caller identities required")
        self.config = config
        self.room_id = room_id
        self.caller_id = caller_id
        self._sequence = -1
        self._last_state: str | None = None
        self._queue: asyncio.Queue[tuple[str, dict, asyncio.Future | None]] = asyncio.Queue(128)
        self._ready: asyncio.Future | None = None
        self._task: asyncio.Task | None = None
        self._closed = False
        self._catalog: dict | None = None

    def _observation(self, state: str, *, interrupted: bool = False) -> dict:
        self._sequence += 1
        return {
            "schema": SCHEMA,
            "event_id": f"presence-{uuid.uuid4().hex}",
            "session_id": self.room_id,
            "performer_id": self.config.performer_id,
            "sequence": self._sequence,
            "state": state,
            "source": "agent_state",
            "interrupted": interrupted,
        }

    def observe(self, state: Literal["idle", "listening", "speaking"]) -> None:
        """Non-blocking LiveKit state callback; no real playback timing claim."""
        if self._closed or self._task is None:
            return
        if state not in {"idle", "listening", "speaking"}:
            raise ValueError("invalid presence state")
        self._last_state = state
        self._enqueue("conversation_presence", {"event": self._observation(state)}, None)

    def _enqueue(self, tool: str, arguments: dict, result: asyncio.Future | None) -> None:
        try:
            self._queue.put_nowait((tool, arguments, result))
        except asyncio.QueueFull:
            # Do not silently report success or reorder older observations.
            self._closed = True
            logger.error("Puppeteer embodiment queue overflow; disabled for this room")
            if result is not None and not result.done():
                result.set_exception(RuntimeError("embodiment queue full"))

    async def start(self) -> dict:
        if self._task is not None:
            raise RuntimeError("embodiment session already started")
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._task = asyncio.create_task(self._serve(), name="puppeteer-embodiment")
        try:
            return await asyncio.wait_for(self._ready, 20)
        except BaseException:
            self._closed = True
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            raise

    async def _serve(self) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            # Do not forward the voice worker's LLM, Hermes or LiveKit secrets.
            params = StdioServerParameters(
                command=self.config.command,
                args=["--config", str(self.config.config_path)],
                env={},
            )
            async with (
                stdio_client(params) as (reader, writer),
                ClientSession(reader, writer) as session,
            ):
                await session.initialize()
                discovered = {tool.name for tool in (await session.list_tools()).tools}
                if not REQUIRED_TOOLS <= discovered:
                    raise RuntimeError("Puppeteer embodiment contract tools unavailable")
                catalog = await self._call(session, "performer_discover", {})
                if (catalog.get("schema") != SCHEMA
                        or catalog.get("execution_mode") != "fake"
                        or catalog.get("performer_id") != self.config.performer_id
                        or catalog.get("physical_qualification") != "not_qualified"):
                    raise RuntimeError("Puppeteer returned incompatible embodiment")
                self._catalog = catalog
                if self._ready is not None and not self._ready.done():
                    self._ready.set_result(catalog)
                while True:
                    try:
                        tool, arguments, result = await asyncio.wait_for(
                            self._queue.get(), timeout=10
                        )
                    except TimeoutError:
                        if self._last_state is not None and not self._closed:
                            tool = "conversation_presence"
                            arguments = {"event": self._observation(self._last_state)}
                            result = None
                        else:
                            continue
                    if tool == "_close":
                        # Disconnect is observation only, not device STOP.
                        await self._call(session, "conversation_presence", {
                            "event": self._observation("disconnected")
                        })
                        if result is not None and not result.done():
                            result.set_result({})
                        break
                    try:
                        value = await self._call(session, tool, arguments)
                        if result is not None and not result.done():
                            result.set_result(value)
                        elif tool == "conversation_presence" and value.get("outcome") not in {
                            "accepted", "duplicate"
                        }:
                            logger.warning("Puppeteer presence rejected: %s", value.get("outcome"))
                    except Exception as exc:  # noqa: BLE001 - MCP transport errors may be arbitrary
                        if result is not None and not result.done():
                            result.set_exception(RuntimeError("Puppeteer call unconfirmed"))
                        else:
                            logger.warning("Puppeteer presence call failed: %s", type(exc).__name__)
        except BaseException as exc:
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(RuntimeError("Puppeteer embodiment startup failed"))
            self._closed = True
            while not self._queue.empty():
                _, _, result = self._queue.get_nowait()
                if result is not None and not result.done():
                    result.set_exception(RuntimeError("Puppeteer embodiment disconnected"))
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.warning("Puppeteer embodiment connection ended: %s", type(exc).__name__)

    @staticmethod
    async def _call(session, tool: str, arguments: dict) -> dict:
        response = await session.call_tool(tool, arguments)
        if response.is_error:
            raise RuntimeError("Puppeteer MCP tool failed; action outcome may be unknown")
        value = response.structured_content
        if not isinstance(value, dict):
            raise TypeError("Unexpected Puppeteer MCP response")
        return value

    async def _request(self, tool: str, arguments: dict) -> dict:
        if self._closed or self._task is None or self._task.done():
            return {"outcome": "failed", "reason": "embodiment_unavailable"}
        result = asyncio.get_running_loop().create_future()
        self._enqueue(tool, arguments, result)
        try:
            return await asyncio.wait_for(result, 15)
        except (TimeoutError, RuntimeError):
            # Unknown outcomes remain unknown; no retry.
            return {"outcome": "failed", "reason": "outcome_unknown", "retry_safe": False}

    async def discover(self) -> dict:
        return await self._request("performer_discover", {})

    async def express(self, kind: str, name: str, asset_revision: str) -> dict:
        if kind not in {"emotion", "dance", "gesture"}:
            return {"outcome": "unsupported", "reason": "unknown_expression_kind"}
        return await self._request("expression_request", {
            "request": {
                "schema": SCHEMA,
                "action_id": f"expr-{uuid.uuid4().hex}",
                "caller_id": self.caller_id,
                "session_id": self.room_id,
                "performer_id": self.config.performer_id,
                "kind": kind,
                "name": name,
                "asset_revision": asset_revision,
            }
        })

    async def lookup(self, action_id: str) -> dict:
        return await self._request("action_lookup", {"action_id": action_id})

    async def close(self) -> None:
        if self._task is None:
            return
        if not self._task.done() and not self._closed:
            self._closed = True
            future = asyncio.get_running_loop().create_future()
            self._enqueue("_close", {}, future)
            try:
                await asyncio.wait_for(future, 5)
            except (TimeoutError, RuntimeError):
                logger.warning("Puppeteer disconnect confirmation unavailable")
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
