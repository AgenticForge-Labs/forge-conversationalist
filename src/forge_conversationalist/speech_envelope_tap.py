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

from .identified_playback import tag_audio_frame
from .native_speech_feature import NativeSpeechFeatureAnalyzer
from .speech_envelope import SpeechEnvelopeAnalyzer

logger = logging.getLogger(__name__)
BATCH_HOPS = 4
NATIVE_BATCH_HOPS = 1

SpeechSink = Callable[[list[dict[str, object]]], bool]


async def observe_synthesized_audio(
    frames: AsyncIterable[object],
    sink: SpeechSink,
    *,
    stream_id: str,
    native_sink: SpeechSink | None = None,
    tag_native_output: bool = False,
    native_begin: Callable[[str], bool] | None = None,
    native_abort: Callable[[str], None] | None = None,
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
    native_analyzer: NativeSpeechFeatureAnalyzer | None = None
    native_pending: list[dict[str, object]] = []
    native_observing = native_sink is not None
    native_begun = False
    native_offered = False

    def abort_native() -> None:
        if native_abort is not None:
            try:
                native_abort(stream_id)
            except Exception:  # noqa: BLE001 - optional observer cannot break voice
                logger.warning("Optional native speech abort failed")

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

    def offer_native() -> bool:
        nonlocal native_pending, native_offered
        if not native_pending:
            return True
        batch = native_pending
        native_pending = []
        try:
            accepted = native_sink is not None and native_sink(batch) is True
            native_offered = native_offered or accepted
            return accepted
        except Exception:  # noqa: BLE001 - optional observer cannot break voice
            logger.warning("Optional native speech feature sink failed")
            return False

    completed = False
    try:
        async for frame in frames:
            if native_observing:
                try:
                    if tag_native_output and not native_begun:
                        try:
                            reserved = native_begin is not None and native_begin(stream_id)
                        except Exception:  # noqa: BLE001 - optional observer cannot break voice
                            reserved = False
                        if not reserved:
                            raise ValueError("native output stream could not be reserved")
                        native_begun = True
                    if tag_native_output and not tag_audio_frame(frame, stream_id):
                        raise ValueError("output frame cannot carry a segment identity")
                    if native_analyzer is None:
                        native_analyzer = NativeSpeechFeatureAnalyzer(
                            sample_rate=frame.sample_rate, channels=frame.num_channels,
                        )
                    for hop in native_analyzer.feed_frame(frame):
                        native_pending.append(hop.as_wire(stream_id=stream_id))
                        if len(native_pending) == NATIVE_BATCH_HOPS and not offer_native():
                            native_observing = False
                            native_pending = []
                            abort_native()
                            break
                except (TypeError, ValueError, AttributeError):
                    logger.warning("Optional native speech analysis disabled for TTS stream")
                    native_observing = False
                    native_pending = []
                    abort_native()
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
        completed = True
    finally:
        if not completed and native_observing:
            abort_native()
    if observing:
        offer()
    if native_observing and (not offer_native() or (native_begun and not native_offered)):
        abort_native()


def agent_class_with_speech_envelope(
    base_agent_class: type, sink: SpeechSink, *, native_sink: SpeechSink | None = None,
    tag_native_output: bool = False,
    native_begin: Callable[[str], bool] | None = None,
    native_abort: Callable[[str], None] | None = None,
) -> type:
    """Build a LiveKit-compatible Agent subclass without moving audio ownership.

    Deliberately uses Agent.default.tts_node so provider selection, sample
    generation, stream flushing and LiveKit's own output handling remain
    unchanged. Does not wrap microphone/STT or spawn a second audio player.
    """
    if not callable(sink):
        raise TypeError("nonblocking speech envelope sink required")
    if native_sink is not None and not callable(native_sink):
        raise TypeError("nonblocking native speech sink required")
    if tag_native_output and (native_sink is None or not callable(native_begin)):
        raise ValueError("segment tagging requires native speech reservation and sink")
    if native_abort is not None and not callable(native_abort):
        raise TypeError("native speech abort callback must be callable")

    class SpeechEnvelopeAgent(base_agent_class):
        async def tts_node(self, text, model_settings):
            source = base_agent_class.default.tts_node(self, text, model_settings)
            if inspect.isawaitable(source):
                source = await source
            async for frame in observe_synthesized_audio(
                source, sink, stream_id="tts-" + uuid.uuid4().hex, native_sink=native_sink,
                tag_native_output=tag_native_output, native_begin=native_begin,
                native_abort=native_abort,
            ):
                yield frame

    SpeechEnvelopeAgent.__name__ = "SpeechEnvelopeAgent"
    return SpeechEnvelopeAgent
