"""One model call, one reply: a leading ``[gesture: NAME]`` tag starts a robot gesture.

The model begins its reply with an optional tag. This module strips the tag from the text
stream before the voice engine sees it (so it is never spoken), starts the gesture on the
robot straight away, and holds the first words back until the gesture is partway through, so
the robot gestures and then talks without a second model round trip.

Only a tag at the very start of a reply acts. A tag anywhere else is removed from the speech
and ignored. The model never moves the robot directly: the worker validates the name against
what each robot offers and asks Puppeteer, which revalidates it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterable, Awaitable, Callable

logger = logging.getLogger(__name__)

_TAG = re.compile(r"^\[\s*gesture\s*:\s*([A-Za-z0-9_-]{1,32})\s*\]\s*", re.IGNORECASE)
# Longest tag text we will hold back while waiting for the closing bracket.
_MAX_TAG = 48
#: The first words are held until this fraction of the gesture has run.
SPEAK_AFTER_FRACTION = 0.5
SPEAK_AFTER_MAX_S = 3.0
DEFAULT_GESTURE_S = 2.0


class GestureTagFilter:
    """Streaming filter: removes gesture tags and reports a tag found at the very start."""

    def __init__(self) -> None:
        self._held = ""
        self._seen_text = False
        self._trim = False  # a tag was just removed: don't start speech with its spacing
        self.started: str | None = None

    def feed(self, text: str) -> str:
        """Return the text to speak from ``text``, holding back a possible partial tag."""
        out: list[str] = []
        buffer = self._held + text
        self._held = ""
        while buffer:
            bracket = buffer.find("[")
            if bracket < 0:
                out.append(buffer)
                self._seen_text = self._seen_text or bool(buffer.strip())
                break
            before = buffer[:bracket]
            if before:
                out.append(before)
                self._seen_text = self._seen_text or bool(before.strip())
            rest = buffer[bracket:]
            match = _TAG.match(rest)
            if match:
                if not self._seen_text and self.started is None:
                    self.started = match.group(1).lower()
                buffer = rest[match.end():]
                self._trim = True
                continue
            close = rest.find("]")
            if close < 0 and len(rest) < _MAX_TAG:
                self._held = rest  # might still become a tag
                break
            # Not a tag: speak the bracket and carry on after it.
            out.append("[")
            self._seen_text = True
            buffer = rest[1:]
        text = "".join(out)
        if self._trim:
            trimmed = text.lstrip()
            if trimmed:
                self._trim = False
            text = trimmed
        return text

    def flush(self) -> str:
        """End of reply: release anything held that never became a tag."""
        held, self._held = self._held, ""
        return held


async def start_gesture(ensemble: object, name: str) -> float:
    """Ask every available robot that offers ``name`` to do it; returns the speech delay (s).

    Returns 0 when nothing accepted it (unknown name, resting, unavailable): the reply is then
    spoken at once and no gesture happens.
    """
    try:
        state = await ensemble.status()
    except Exception:  # noqa: BLE001 - a lost robot link must not silence the voice
        logger.warning("gesture tag ignored: robot status unavailable")
        return 0.0
    performers = state.get("performers") if isinstance(state, dict) else None
    if not isinstance(performers, dict):
        return 0.0
    longest = 0.0
    for performer_id, entry in sorted(performers.items()):
        if not isinstance(entry, dict) or entry.get("status") in {"blocked", "unavailable"}:
            continue
        if name not in (entry.get("expressions") or ()):
            continue
        try:
            result = await ensemble.express(name, performer_id)
        except Exception:  # noqa: BLE001
            logger.warning("gesture tag failed for %s", performer_id)
            continue
        if result.get("outcome") != "accepted":
            logger.info("gesture tag %s not accepted for %s: %s",
                        name, performer_id, result.get("reason") or result.get("outcome"))
            continue
        durations = entry.get("expression_durations_s")
        seconds = durations.get(name) if isinstance(durations, dict) else None
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
            seconds = DEFAULT_GESTURE_S
        longest = max(longest, float(seconds))
    return min(SPEAK_AFTER_MAX_S, longest * SPEAK_AFTER_FRACTION)


def _content(chunk: object) -> str | None:
    delta = getattr(chunk, "delta", None)
    content = getattr(delta, "content", None)
    return content if isinstance(content, str) else None


async def filter_gesture_stream(
    stream: AsyncIterable[object],
    ensemble: object,
    *,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> AsyncIterable[object]:
    """Wrap an LLM node stream: strip tags, start the gesture, delay the first words."""
    tags = GestureTagFilter()
    delayed = False
    async for chunk in stream:
        if isinstance(chunk, str):
            text = chunk
        else:
            text = _content(chunk)
        if text is None:
            yield chunk  # tool calls, usage, flush markers pass through untouched
            continue
        cleaned = tags.feed(text)
        if tags.started is not None and not delayed:
            delayed = True
            delay = await start_gesture(ensemble, tags.started)
            if delay > 0:
                await sleep(delay)
        if not cleaned:
            continue
        if isinstance(chunk, str):
            yield cleaned
        else:
            yield chunk.model_copy(update={
                "delta": chunk.delta.model_copy(update={"content": cleaned})})
    tail = tags.flush()
    if tail:
        yield tail


def agent_class_with_gesture_tags(base_agent_class: type, ensemble: object) -> type:
    """Subclass the agent so its LLM output is filtered through :func:`filter_gesture_stream`."""

    class GestureTagAgent(base_agent_class):
        async def llm_node(self, chat_ctx, tools, model_settings):
            source = base_agent_class.default.llm_node(self, chat_ctx, tools, model_settings)
            if asyncio.iscoroutine(source):
                source = await source
            async for chunk in filter_gesture_stream(source, ensemble):
                yield chunk

    GestureTagAgent.__name__ = "GestureTagAgent"
    return GestureTagAgent
