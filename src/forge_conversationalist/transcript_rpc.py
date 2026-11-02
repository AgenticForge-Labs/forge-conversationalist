"""Authorized, read-only retrieval of the current LiveKit session transcript.

The transcript lives only in the running ``AgentSession.history``. This RPC
does not persist history or expose system prompts, tool calls, or other hidden
chat-context items.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

MAX_TRANSCRIPT_MESSAGES = 50
MAX_TRANSCRIPT_TEXT_CHARS = 2_000
MAX_TRANSCRIPT_RESPONSE_BYTES = 12_000


def transcript_messages(history: object) -> list[dict[str, str]]:
    """Return a bounded visible transcript from a LiveKit ChatContext."""

    messages_method = getattr(history, "messages", None)
    if not callable(messages_method):
        raise TypeError("history must provide LiveKit ChatContext.messages()")

    visible: list[dict[str, str]] = []
    for message in messages_method():
        role = getattr(message, "role", None)
        item_id = getattr(message, "id", None)
        text = getattr(message, "text_content", None)
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(item_id, str) or not item_id:
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        visible.append({
            "id": item_id,
            "role": role,
            "text": text.strip()[:MAX_TRANSCRIPT_TEXT_CHARS],
        })

    # Keep the newest messages and enforce a conservative serialized payload
    # bound for LiveKit RPC, accounting for JSON escaping and UTF-8 bytes.
    result: list[dict[str, str]] = []
    for message in reversed(visible[-MAX_TRANSCRIPT_MESSAGES:]):
        candidate = [message, *reversed(result)]
        encoded = json.dumps({"messages": candidate}, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_TRANSCRIPT_RESPONSE_BYTES:
            continue
        result.insert(0, message)
    return result


def transcript_payload(history: object) -> str:
    """Serialize the public response shape used by ``forge.get_transcript``."""

    return json.dumps(
        {"messages": transcript_messages(history)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def register_transcript_rpc(
    local_participant: object,
    *,
    history: object,
    authorized_identity: str,
    room: object | None = None,
    participant_wait_timeout: float = 5.0,
    rpc_error_factory: Callable[[str], Exception] | None = None,
) -> Callable[[object], object]:
    """Register ``forge.get_transcript`` for exactly one authorized identity.

    The LiveKit RPC sender identity is checked before reading history. The
    request payload is ignored; it cannot select a room, participant, or
    history source. ``rpc_error_factory`` exists for isolated unit tests; in
    production the SDK's application-level RPC error is used.
    """

    if not authorized_identity:
        raise ValueError("authorized_identity is required")
    if participant_wait_timeout <= 0:
        raise ValueError("participant_wait_timeout must be positive")
    register = getattr(local_participant, "register_rpc_method", None)
    if not callable(register):
        raise TypeError("participant must provide LiveKit register_rpc_method()")

    def unauthorized(message: str) -> Exception:
        if rpc_error_factory is not None:
            return rpc_error_factory(message)
        from livekit import rtc

        return rtc.RpcError(
            code=rtc.RpcError.ErrorCode.APPLICATION_ERROR,
            message=message,
        )

    async def get_transcript(data: object) -> str:
        if getattr(data, "caller_identity", None) != authorized_identity:
            raise unauthorized("Only the authorized room participant can read this transcript")
        if room is not None:
            try:
                await _wait_for_room_participant(
                    room, authorized_identity, timeout=participant_wait_timeout
                )
            except TimeoutError:
                raise unauthorized(
                    "Authorized participant is not ready in the agent room; retry transcript request"
                )
        return transcript_payload(history)

    register("forge.get_transcript", get_transcript)
    return get_transcript


async def _wait_for_room_participant(room: object, identity: str, *, timeout: float) -> None:
    """Wait for the authorized caller to appear in this worker's room view.

    Register before checking ``remote_participants`` so a join occurring
    between the check and await cannot be missed. Always detach the temporary
    listener, including on timeout or cancellation.
    """

    on = getattr(room, "on", None)
    off = getattr(room, "off", None)
    remote_participants = getattr(room, "remote_participants", None)
    if not callable(on) or not callable(off) or remote_participants is None:
        raise TypeError("room must provide LiveKit participant events and remote_participants")

    connected = asyncio.Event()

    def on_participant_connected(participant: object) -> None:
        if getattr(participant, "identity", None) == identity:
            connected.set()

    on("participant_connected", on_participant_connected)
    try:
        if identity in remote_participants:
            return
        await asyncio.wait_for(connected.wait(), timeout=timeout)
    finally:
        off("participant_connected", on_participant_connected)
