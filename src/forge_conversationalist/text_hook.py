"""Backend hook for completed assistant text returned by a LiveKit session.

This is a callback seam, not a queue or message bus. Install it after creating
the session and before ``session.start``::

    text_hook = install_assistant_text_hook(session, handle_assistant_text)

It receives finalized assistant text plus a content-free completion signal,
so it is independent of browser delivery and audio output. LiveKit emits this
event after playout completes, including the message's interrupted flag. It
does not observe partial LLM tokens, publish text to a room, play audio, or
command a robot. Downstream consumers must apply their own privacy and
capability checks.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AssistantTextCompleted:
    """Content-free signal emitted after assistant playout has completed."""

    item_id: str
    interrupted: bool


TextCallback = Callable[[AssistantTextCompleted, str], None]


@dataclass(frozen=True)
class ConversationStateChanged:
    """Content-free backend state signal mapped from a LiveKit session event."""

    state: Literal["idle", "listening", "speaking"]
    source: Literal["agent", "user"]
    interrupted: bool = False


StateCallback = Callable[[ConversationStateChanged], None]


class AssistantTextHook:
    """Observe completed assistant messages and invoke a backend callback.

    LiveKit emits the assistant ``conversation_item_added`` after speech
    playout completes. It is therefore a completion notification, not a signal
    that the agent is currently speaking. The callback receives a content-free
    signal separately from the finalized text. The signal preserves LiveKit's
    interrupted flag. Message IDs suppress duplicate delivery within a bounded
    per-session cache.
    """

    def __init__(self, callback: TextCallback, *, max_seen: int = 512) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        if not 1 <= max_seen <= 4096:
            raise ValueError("max_seen must be between 1 and 4096")
        self._callback = callback
        self._seen: set[str] = set()
        self._order: deque[str] = deque()
        self._limit = max_seen

    def __call__(self, event: object) -> None:
        item = getattr(event, "item", None)
        if getattr(item, "role", None) != "assistant":
            return

        item_id = getattr(item, "id", None)
        text = getattr(item, "text_content", None)
        if not isinstance(item_id, str) or not item_id or not isinstance(text, str):
            return
        text = text.strip()
        if not text or item_id in self._seen:
            return

        self._order.append(item_id)
        self._seen.add(item_id)
        while len(self._order) > self._limit:
            self._seen.remove(self._order.popleft())

        signal = AssistantTextCompleted(
            item_id=hashlib.sha256(item_id.encode()).hexdigest()[:32],
            interrupted=bool(getattr(item, "interrupted", False)),
        )
        self._callback(signal, text)


def install_assistant_text_hook(session: object, callback: TextCallback) -> AssistantTextHook:
    """Register the hook on a LiveKit ``AgentSession`` and return its handle.

    Keep the returned handle and call ``remove_assistant_text_hook`` when the
    session is torn down. This uses LiveKit's existing event emitter directly.
    """

    hook = AssistantTextHook(callback)
    on = getattr(session, "on", None)
    if not callable(on):
        raise TypeError("session must provide LiveKit's on(event, callback) API")
    on("conversation_item_added", hook)
    return hook


def remove_assistant_text_hook(session: object, hook: AssistantTextHook) -> None:
    """Unregister a hook installed with :func:`install_assistant_text_hook`."""

    off = getattr(session, "off", None)
    if not callable(off):
        raise TypeError("session must provide LiveKit's off(event, callback) API")
    off("conversation_item_added", hook)


class SessionStateHook:
    """Map trusted LiveKit agent/user state changes to generic conversation state.

    This reports semantic state only. It is intentionally separate from the
    post-playout assistant text callback and performs no presentation or motion.
    """

    def __init__(self, callback: StateCallback) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._callback = callback
        self._last_state: str | None = None
        self._agent_state: str | None = None
        self._user_state: str | None = None

    def on_agent_state_changed(self, event: object) -> None:
        new_state = getattr(event, "new_state", None)
        if new_state not in {"idle", "listening", "speaking", "thinking", "initializing"}:
            return
        self._agent_state = new_state
        self._refresh("agent")

    def on_user_state_changed(self, event: object) -> None:
        new_state = getattr(event, "new_state", None)
        if new_state not in {"speaking", "listening", "away"}:
            return
        self._user_state = new_state
        self._refresh("user")

    def _refresh(self, source: Literal["agent", "user"]) -> None:
        # A user's speech is evidence of an interruption/turn and maps to
        # listening even while LiveKit still reports agent speaking. In
        # contrast, a routine user-listening event must not cancel a live
        # speaking state. Thinking/initializing are deliberately left unmapped.
        if self._user_state == "speaking":
            mapped = "listening"
        elif self._agent_state == "speaking":
            mapped = "speaking"
        elif self._user_state == "away":
            mapped = "idle"
        elif self._agent_state == "listening":
            mapped = "listening"
        elif self._agent_state == "idle":
            mapped = "idle"
        else:
            return
        interrupted = (
            source == "user" and self._user_state == "speaking"
            and self._agent_state == "speaking" and self._last_state == "speaking"
        )
        self._emit(mapped, source, interrupted=interrupted)

    def _emit(self, state: str, source: Literal["agent", "user"], *, interrupted: bool) -> None:
        if state == self._last_state:
            return
        self._last_state = state
        self._callback(ConversationStateChanged(state=state, source=source, interrupted=interrupted))


def install_session_state_hook(session: object, callback: StateCallback) -> SessionStateHook:
    """Subscribe to backend LiveKit state events before ``session.start``."""

    hook = SessionStateHook(callback)
    on = getattr(session, "on", None)
    if not callable(on):
        raise TypeError("session must provide LiveKit's on(event, callback) API")
    on("agent_state_changed", hook.on_agent_state_changed)
    on("user_state_changed", hook.on_user_state_changed)
    return hook


def remove_session_state_hook(session: object, hook: SessionStateHook) -> None:
    """Unregister both listeners installed by :func:`install_session_state_hook`."""

    off = getattr(session, "off", None)
    if not callable(off):
        raise TypeError("session must provide LiveKit's off(event, callback) API")
    off("agent_state_changed", hook.on_agent_state_changed)
    off("user_state_changed", hook.on_user_state_changed)
