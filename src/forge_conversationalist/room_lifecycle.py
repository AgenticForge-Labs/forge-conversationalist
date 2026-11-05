"""Room-owner disconnect fences a Puppeteer child without owning device STOP.

LiveKit room and participant events are the authority for voice-room lifetime.
This module only requests the existing EnsembleSession's bounded close; robot
hardware evidence remains with Puppeteer/SDKs.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def install_ensemble_room_shutdown_hooks(
    room: object, ensemble: object, *, authorized_identity: str,
) -> bool:
    """Bind the existing LiveKit room; never reconnect on owner rejoin.

    Returns False when a test/different Room API cannot report events. The
    existing worker shutdown callback still closes the same ensemble.
    """
    on = getattr(room, "on", None)
    if not callable(on):
        return False
    if not authorized_identity:
        raise ValueError("signed room identity required for robot lifecycle")

    task: asyncio.Task | None = None

    def close_once(reason: str) -> None:
        nonlocal task
        if task is not None:
            return
        logger.info(
            "Puppeteer room close requested after LiveKit owner departure",
            extra={"reason": reason, "physical_settling_verified": False},
        )
        # Explicit interruption fences queued speaking/envelope immediately.
        observe = getattr(ensemble, "observe", None)
        if callable(observe):
            try:
                observe("listening", interrupted=True)
            except (RuntimeError, ValueError):
                logger.warning("Puppeteer presence interruption outcome unconfirmed")
        task = asyncio.create_task(ensemble.close(), name="puppeteer-owner-room-close")

    def participant_left(participant: object, *_args: object) -> None:
        if getattr(participant, "identity", None) == authorized_identity:
            close_once("authorized_participant_disconnected")

    def room_disconnected(*_args: object) -> None:
        close_once("livekit_room_disconnected")

    on("participant_disconnected", participant_left)
    on("disconnected", room_disconnected)
    return True
