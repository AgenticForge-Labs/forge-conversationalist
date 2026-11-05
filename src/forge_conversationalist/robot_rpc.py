"""Authorized LiveKit RPC facade for Puppeteer's existing room-owned ensemble.

This module is a transport adapter, not a motor controller or second MCP child.
All actions still enter the same EnsembleSession and Puppeteer ExecutionRuntime.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from .ensemble import SCHEMA, EnsembleSession

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_PAYLOAD_BYTES = 512
_MAX_STATUS_BYTES = 16_384


def register_robot_rpc(
    local_participant: object,
    *,
    ensemble: EnsembleSession,
    authorized_identity: str,
    room: object | None = None,
    capabilities: dict[str, object] | None = None,
    rpc_error_factory: Callable[[str], Exception] | None = None,
) -> None:
    """Expose read-only discovery and explicit one-robot gestures to the owner.

    The authenticated LiveKit RPC caller must match signed job metadata.
    Untrusted payloads never select a new room, config, motor operation,
    arbitrary recording, or broader device authority.
    """
    if not authorized_identity or ensemble is None:
        raise ValueError("Robot RPC requires an authorized owner and ensemble")
    register = getattr(local_participant, "register_rpc_method", None)
    if not callable(register):
        raise TypeError("LiveKit local participant must support RPC registration")

    def reject(reason: str) -> Exception:
        if rpc_error_factory is not None:
            return rpc_error_factory(reason)
        from livekit import rtc

        return rtc.RpcError(code=rtc.RpcError.ErrorCode.APPLICATION_ERROR, message=reason)

    def check_identity(data: object) -> None:
        if getattr(data, "caller_identity", None) != authorized_identity:
            raise reject("Robot controls are restricted to the authorized participant")
        if room is not None and authorized_identity not in getattr(room, "remote_participants", {}):
            raise reject("Authorized participant is not connected to this room")

    async def get_robots(data: object) -> str:
        check_identity(data)
        state = await ensemble.status()
        if state.get("schema") != SCHEMA or not isinstance(state.get("performers"), dict):
            raise reject("Robot catalog is unavailable")
        response = json.dumps(state, separators=(",", ":"), ensure_ascii=True)
        if len(response.encode("utf-8")) > _MAX_STATUS_BYTES:
            raise reject("Robot catalog exceeds the bounded response size")
        return response

    async def request_robot_expression(data: object) -> str:
        check_identity(data)
        payload = getattr(data, "payload", None)
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise reject("Invalid robot expression request")
        try:
            request = json.loads(payload)
        except ValueError:
            raise reject("Invalid robot expression request") from None
        if (not isinstance(request, dict)
                or set(request) != {"performer_id", "expression"}
                or any(not isinstance(value, str) or not _NAME.fullmatch(value)
                       for value in request.values())):
            raise reject("Robot expression requires one performer and one named behavior")
        outcome = await ensemble.express(request["expression"], request["performer_id"])
        # Accepted only means the owner admitted an action, NOT physical motion.
        if outcome.get("outcome") == "accepted":
            outcome = {**outcome, "completion": "unconfirmed"}
        return json.dumps(outcome, separators=(",", ":"), ensure_ascii=True)

    async def get_capabilities(data: object) -> str:
        check_identity(data)
        state = await ensemble.status()
        performers = state.get("performers") if isinstance(state, dict) else None
        summary = {
            "schema": "forge.capabilities.v1",
            "integrations": capabilities or {},
            "performers": {
                name: {
                    "status": detail.get("status"),
                    "expressions": detail.get("expressions", []),
                    "presence_behaviors": detail.get("presence_behaviors", []),
                }
                for name, detail in (performers or {}).items()
                if isinstance(detail, dict)
            },
        }
        response = json.dumps(summary, separators=(",", ":"), ensure_ascii=True)
        if len(response.encode("utf-8")) > _MAX_STATUS_BYTES:
            raise reject("Capability catalog exceeds the bounded response size")
        return response

    async def set_connection(data: object) -> str:
        check_identity(data)
        payload = getattr(data, "payload", None)
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise reject("Invalid robot connection request")
        try:
            request = json.loads(payload)
        except ValueError:
            raise reject("Invalid robot connection request") from None
        if not isinstance(request, dict) or set(request) != {"connected"} or not isinstance(request["connected"], bool):
            raise reject("Robot connection requires one boolean")
        try:
            if request["connected"]:
                state = await ensemble.reconnect()
            else:
                state = await ensemble.close()
        except (RuntimeError, ValueError, TimeoutError):
            raise reject("Robot connection change failed") from None
        confirmed = request["connected"] or state.get("outcome") in {"acknowledged", "not_active"}
        return json.dumps({
            "outcome": "ok" if confirmed else "unknown",
            "state": state, "retry_safe": False,
        }, separators=(",", ":"), ensure_ascii=True)

    register("forge.get_robots", get_robots)
    register("forge.set_robot_connection", set_connection)
    register("forge.get_capabilities", get_capabilities)
    register("forge.request_robot_expression", request_robot_expression)
