"""Output-playback-aware semantic presence for a single LiveKit room.

This is NOT an audio sink, PCM tap or motion controller. It observes events
emitted by the session's LiveKit AudioOutput *after* RoomIO has started.
The default room output typically reports frames published to a track, NOT
physical sound emerging from a user's browser speaker. Source attribution
remains honest and is never treated as sample-accurate synchronization.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .text_hook import ConversationStateChanged

State = Literal["idle", "listening", "speaking"]
Source = Literal["agent_state", "playback"]
PresenceCallback = Callable[[State, Source, bool], None]


class PlaybackPresence:
    """Give authenticated audio-output events priority over semantic 'speaking'.

    As long as no compatible output is attached, every state observation passes
    through unchanged (legacy behavior). After attachment, semantic speaking
    indicates intent only; movement's speaking presence starts on the output's
    playback_started event. On playback_finished or user interruption, the
    latest semantic state is restored without inventing frame timestamps.
    """

    def __init__(self, callback: PresenceCallback) -> None:
        if not callable(callback):
            raise TypeError("playback presence callback is required")
        self._callback = callback
        self._audio_output: object | None = None
        self._playing = False
        self._barge_in = False
        self._last_semantic: State = "idle"
        self._last_emitted: State | None = None

    @property
    def attached(self) -> bool:
        return self._audio_output is not None

    def attach(self, audio_output: object | None) -> bool:
        """Subscribe only after session.start; fail open to legacy state mode.

        The two listeners are synchronous and never access robot hardware.
        This method is intended for exactly one output sink per voice session.
        """
        if self._audio_output is not None:
            raise RuntimeError("audio output already attached")
        if audio_output is None:
            return False
        on = getattr(audio_output, "on", None)
        off = getattr(audio_output, "off", None)
        if not callable(on) or not callable(off):
            return False
        on("playback_started", self._on_started)
        try:
            on("playback_finished", self._on_finished)
        except Exception:
            off("playback_started", self._on_started)
            raise
        self._audio_output = audio_output
        # The SDK may have emitted a semantic 'speaking' before RoomIO attached.
        # No motion should be inferred from that event.
        if self._last_semantic == "speaking":
            self._emit("listening", "agent_state", False)
        return True

    def detach(self) -> None:
        output = self._audio_output
        if output is None:
            return
        self._audio_output = None
        self._playing = False
        off = output.off
        off("playback_started", self._on_started)
        off("playback_finished", self._on_finished)

    def observe(self, signal: ConversationStateChanged) -> None:
        """Keep the semantic state without overriding a live playback interval."""
        self._last_semantic = signal.state
        if not self.attached:
            self._emit(signal.state, "agent_state", signal.interrupted)
            return
        if signal.interrupted:
            self._barge_in = True
            self._playing = False
            self._emit("listening", "agent_state", True)
            return
        if self._barge_in and signal.source == "agent" and signal.state == "speaking":
            # An agent speaking transition after barge-in may begin a fresh
            # utterance. The first playback_started event still gates motion.
            self._barge_in = False
        if self._playing:
            return  # Never override active playout with an early 'idle'.
        mapped: State = "listening" if signal.state == "speaking" else signal.state
        self._emit(mapped, "agent_state", False)

    def _on_started(self, _event: object) -> None:
        if not self.attached or self._barge_in:
            return
        self._playing = True
        self._emit("speaking", "playback", False)

    def _on_finished(self, event: object) -> None:
        if not self.attached or not self._playing:
            return
        self._playing = False
        interrupted = getattr(event, "interrupted", False) is True
        # Treat completed output as evidence that this playback interval ended,
        # not as evidence of any robot command completing or physical settling.
        restored: State = (
            "listening" if self._last_semantic == "speaking" else self._last_semantic
        )
        self._emit(restored, "playback", interrupted)

    def _emit(self, state: State, source: Source, interrupted: bool) -> None:
        if state == self._last_emitted and not interrupted:
            return
        self._last_emitted = state
        self._callback(state, source, interrupted)
