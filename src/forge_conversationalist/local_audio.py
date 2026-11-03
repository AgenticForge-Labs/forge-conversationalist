"""Browserless LiveKit participant using the host's audio devices.

This process owns exactly one local microphone/speaker pair. LiveKit remains
the room and realtime audio transport; the browser is not needed for capture
or playback. A non-default output (including Reachy's USB speaker when the OS
exposes it) can be selected by device index or an unambiguous name substring.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from .config import AGENT_NAME

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalRoom:
    name: str
    identity: str
    token: str


def build_local_room(
    *, api_key: str, api_secret: str, agent_name: str, tts_model: str | None = None,
    tts_voice: str | None = None,
) -> LocalRoom:
    """Create a short-lived signed local participant token and dispatch config."""
    from livekit.api import AccessToken, VideoGrants
    from livekit.protocol.agent_dispatch import RoomAgentDispatch
    from livekit.protocol.room import RoomConfiguration

    room_name = f"conversationalist-local-{uuid4().hex}"
    identity = f"system-audio-{uuid4().hex}"
    metadata: dict[str, str] = {"authorized_identity": identity}
    if tts_model is not None:
        metadata["tts_model"] = tts_model
    if tts_voice is not None:
        metadata["tts_voice"] = tts_voice
    room_config = RoomConfiguration(agents=[RoomAgentDispatch(
        agent_name=agent_name,
        metadata=json.dumps(metadata, separators=(",", ":")),
    )])
    token = (
        AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name("Forge Conversationalist system audio")
        .with_ttl(timedelta(hours=2))
        .with_grants(VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        ))
        .with_room_config(room_config)
        .to_jwt()
    )
    return LocalRoom(name=room_name, identity=identity, token=token)


def resolve_device(devices: list[dict], selector: str | None, *, kind: str) -> int | None:
    """Resolve an enumerated sounddevice index or unique case-insensitive name."""
    if selector is None:
        return None
    if selector.isdecimal():
        index = int(selector)
        if any(item.get("index") == index for item in devices):
            return index
        raise ValueError(f"{kind} device index {index} is not available")

    def normalize(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))

    requested = normalize(selector)
    if not requested:
        raise ValueError(f"{kind} device selector must not be empty")
    matches = [
        item for item in devices
        if requested in normalize(str(item.get("name", "")))
    ]
    if len(matches) != 1:
        if not matches:
            raise ValueError(f"no {kind} device matches {selector!r}; use --list-devices")
        raise ValueError(
            f"{kind} device selector {selector!r} is ambiguous; use its numeric index"
        )
    return int(matches[0]["index"])


def is_agent_audio(track_kind: int, participant_kind: int, *, rtc: object) -> bool:
    """Keep speaker output restricted to the remote agent's audio track."""
    return (
        track_kind == rtc.TrackKind.KIND_AUDIO
        and participant_kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
    )


async def wait_for_agent(ready: asyncio.Event, stopping: asyncio.Event, *, timeout: float = 60) -> None:
    """Wait for the dispatched worker, but fail promptly if the room disconnects."""
    if ready.is_set():
        return
    ready_task = asyncio.create_task(ready.wait())
    stopping_task = asyncio.create_task(stopping.wait())
    tasks = {ready_task, stopping_task}
    try:
        done, _ = await asyncio.wait(
            tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED,
        )
        if stopping_task in done or stopping.is_set():
            raise ConnectionError("LiveKit room disconnected before the agent joined")
        if ready_task in done and ready.is_set():
            return
        raise TimeoutError("Conversationalist agent did not join the room within 60 seconds")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def list_devices() -> None:
    from livekit import rtc

    devices = rtc.MediaDevices()
    print("Input devices (use --input-device INDEX or a unique name substring):")
    for item in devices.list_input_devices():
        print(f"  {item['index']}: {item['name']}")
    print("Output devices (use --output-device INDEX or a unique name substring):")
    for item in devices.list_output_devices():
        print(f"  {item['index']}: {item['name']}")


async def run_local_audio(
    *,
    livekit_url: str,
    api_key: str,
    api_secret: str,
    input_selector: str | None = None,
    output_selector: str | None = None,
    agent_name: str = AGENT_NAME,
) -> None:
    """Connect the host mic/speaker to one authorized Conversationalist room."""
    from livekit import api, rtc
    from livekit.api import DeleteRoomRequest

    room_info = build_local_room(
        api_key=api_key,
        api_secret=api_secret,
        agent_name=agent_name,
    )
    media = rtc.MediaDevices(loop=asyncio.get_running_loop())
    input_index = resolve_device(media.list_input_devices(), input_selector, kind="input")
    output_index = resolve_device(media.list_output_devices(), output_selector, kind="output")

    room = rtc.Room()
    output_player = None
    input_capture = None
    connected = False
    subscribed_tracks: set[str] = set()
    track_tasks: set[asyncio.Task] = set()
    stopping = asyncio.Event()
    agent_ready = asyncio.Event()

    def participant_connected(participant) -> None:
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            agent_ready.set()

    def participant_disconnected(participant) -> None:
        if (
            participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
            and agent_ready.is_set()
        ):
            stopping.set()

    def track_subscribed(track, _publication, participant) -> None:
        # Only render the LiveKit agent's audio. Never loop the local mic back.
        if (
            not is_agent_audio(track.kind, participant.kind, rtc=rtc)
            or track.sid in subscribed_tracks
        ):
            return
        subscribed_tracks.add(track.sid)

        async def add_track() -> None:
            try:
                await output_player.add_track(track)
                logger.info("Subscribed to agent audio track")
            except Exception:
                subscribed_tracks.discard(track.sid)
                logger.exception("Could not attach agent audio to the selected speaker")

        task = asyncio.create_task(add_track())
        track_tasks.add(task)
        task.add_done_callback(track_tasks.discard)

    def track_unsubscribed(track, _publication, _participant) -> None:
        subscribed_tracks.discard(track.sid)
        task = asyncio.create_task(output_player.remove_track(track))
        track_tasks.add(task)
        task.add_done_callback(track_tasks.discard)

    room.on("track_subscribed", track_subscribed)
    room.on("track_unsubscribed", track_unsubscribed)
    room.on("participant_connected", participant_connected)
    room.on("participant_disconnected", participant_disconnected)
    room.on("disconnected", lambda *_: stopping.set())

    try:
        # Prepare the selected output before the room can deliver an agent
        # track, so an early subscription callback cannot race initialization.
        output_player = media.open_output(output_device=output_index)
        await output_player.start()
        # Connect before starting capture so no microphone samples are sent to
        # a room until its signed identity and authorized agent dispatch exist.
        await room.connect(livekit_url, room_info.token)
        connected = True
        for participant in room.remote_participants.values():
            participant_connected(participant)
        logger.info("Waiting for the Conversationalist agent before opening the microphone")
        await wait_for_agent(agent_ready, stopping)
        input_capture = media.open_input(
            input_device=input_index,
            enable_aec=True,
            noise_suppression=True,
            high_pass_filter=True,
            auto_gain_control=True,
        )
        microphone = rtc.LocalAudioTrack.create_audio_track(
            "system-microphone", input_capture.source,
        )
        await room.local_participant.publish_track(
            microphone,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        input_name = "system default" if input_index is None else str(input_index)
        output_name = "system default" if output_index is None else str(output_index)
        logger.info(
            "Browserless voice session ready in room %s (input %s, output %s). "
            "Press Ctrl+C to end.",
            room_info.name,
            input_name,
            output_name,
        )
        await stopping.wait()
    finally:
        if input_capture is not None:
            try:
                await input_capture.aclose()
            except Exception:
                logger.exception("Could not close the local microphone")
        if connected:
            try:
                await room.disconnect()
            except Exception:
                logger.exception("Could not disconnect the local LiveKit participant")
        if track_tasks:
            await asyncio.gather(*track_tasks, return_exceptions=True)
        if output_player is not None:
            try:
                await output_player.aclose()
            except Exception:
                logger.exception("Could not close the local speaker")
        # The worker keeps sessions open for browser reconnection, so this
        # dedicated CLI owns cleanup of the exact ephemeral room it created.
        if connected:
            api_url = livekit_url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
            try:
                async with api.LiveKitAPI(
                    api_url, api_key=api_key, api_secret=api_secret,
                ) as client:
                    await client.room.delete_room(DeleteRoomRequest(room=room_info.name))
            except Exception:
                logger.exception("Could not delete the local audio room; it may expire by timeout")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Conversationalist with system audio, without a browser."
    )
    parser.add_argument("--list-devices", action="store_true", help="list local input/output devices")
    parser.add_argument("--input-device", help="system input default, numeric device index, or unique name substring")
    parser.add_argument("--output-device", help="system output default, numeric device index, or unique name substring")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser.parse_args()


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(".env.local")
    args = _arguments()
    logging.basicConfig(level=getattr(logging, args.log_level))
    if args.list_devices:
        asyncio.run(list_devices())
        return

    livekit_url = os.getenv("LIVEKIT_URL", "").strip()
    api_key = os.getenv("LIVEKIT_API_KEY", "").strip()
    api_secret = os.getenv("LIVEKIT_API_SECRET", "").strip()
    if not livekit_url or not api_key or not api_secret:
        raise SystemExit("LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET are required")
    try:
        asyncio.run(run_local_audio(
            livekit_url=livekit_url,
            api_key=api_key,
            api_secret=api_secret,
            input_selector=args.input_device,
            output_selector=args.output_device,
        ))
    except KeyboardInterrupt:
        logger.info("Local audio session interrupted by operator")


if __name__ == "__main__":
    main()
