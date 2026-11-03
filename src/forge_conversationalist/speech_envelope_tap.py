"""Opt-in, content-free synthesized audio observation via LiveKit's TTS node.

This is a pass-through generator: the exact original AudioFrame objects are
yielded in order to LiveKit. The feature sink is synchronous and best-effort
and must not block for network/MCP. No microphone data or transcript is read.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import AsyncIterable, Callable

from .speech_envelope import SpeechEnvelopeAnalyzer

logger = logging.getLogger(__name__)
BATCH_HOPS = 4

SpeechSink = Callable[[list[dict[str, object]]], bool]


async def observe_synthesized_audio(
    frames: AsyncIterable[object],
    sink: SpeechSink,
    *,
    stream_id: str,
) -> AsyncIterable[object]:
    """Pass frames through unchanged, offering at most four 50-ms hops per batch.

    Drops feature observation on backpressure, incompatible output format or
    sink errors without degrading audible speech. Offset is relative to
    synthesized PCM, not LiveKit track or speaker playout. A new stream_id is
    required for each independent TTS invocation.
    """
    analyzer: SpeechEnvelopeAnalyzer | None = None
    pending: list[dict[str, object]] = []
    observing = True

    def offer() -> bool:
        nonlocal pending
        if not pending:
            return True
        batch = pending
        pending = []
        try:
            return sink(batch) is True
        except Exception:  # noqa: BLE001 - optional observer cannot break voice
            logger.warning("Optional synthesized speech envelope sink failed")
            return False

    async for frame in frames:
        if observing:
            try:
                if analyzer is None:
                    analyzer = SpeechEnvelopeAnalyzer(
                        sample_rate=frame.sample_rate, channels=frame.num_channels,
                    )
                for hop in analyzer.feed_frame(frame):
                    pending.append(hop.as_wire(stream_id=stream_id))
                    if len(pending) == BATCH_HOPS and not offer():
                        observing = False
                        pending = []
                        break
            except (TypeError, ValueError, AttributeError):
                logger.warning("Optional synthesized speech analysis disabled for TTS stream")
                observing = False
                pending = []
        yield frame
    if observing:
        offer()


def agent_class_with_speech_envelope(base_agent_class: type, sink: SpeechSink) -> type:
    """Build a LiveKit-compatible Agent subclass without moving audio ownership.

    Deliberately uses Agent.default.tts_node so provider selection, sample
    generation, stream flushing and LiveKit's own output handling remain
    unchanged. Does not wrap microphone/STT or spawn a second audio player.
    """
    if not callable(sink):
        raise TypeError("nonblocking speech envelope sink required")

    class SpeechEnvelopeAgent(base_agent_class):
        async def tts_node(self, text, model_settings):
            source = base_agent_class.default.tts_node(self, text, model_settings)
            if inspect.isawaitable(source):
                source = await source
            async for frame in observe_synthesized_audio(
                source, sink, stream_id="tts-" + uuid.uuid4().hex,
            ):
                yield frame

    SpeechEnvelopeAgent.__name__ = "SpeechEnvelopeAgent"
    return SpeechEnvelopeAgent
