"""Exercise the real SO-ARM SDK simulator through Conversationalist's client.

No voice service or physical device is opened. Physical configurations are
explicitly rejected; use the connected robot MCP for attended hardware runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sqlite3
import tempfile
import uuid
from pathlib import Path

from forge_conversationalist.ensemble import EnsembleSession, PuppeteerEnsembleConfig


async def _check(config_path: Path, command: str) -> dict:
    config = PuppeteerEnsembleConfig(config_path.resolve(), command=command)
    config.preflight()
    data = json.loads(config_path.read_text())
    refs = data.get("robots", [])
    if data.get("schema_version") != 4 or len(refs) != 1 or refs[0].get("kind") != "soarm101":
        raise ValueError("check requires a SO-ARM-only v4 simulation room")
    robot_path = Path(refs[0]["file"])
    if not robot_path.is_absolute():
        robot_path = config_path.parent / robot_path
    provider = json.loads(robot_path.read_text())["provider"]
    if provider.get("mode") != "sdk_simulation_daemon":
        raise ValueError("this check only opens an SDK simulation daemon")
    journal = Path(data["action_journal"])
    room = EnsembleSession(config, room_id=f"soarm-check-{uuid.uuid4().hex}", caller_id="local-acceptance")
    # Unique per invocation: completed actions from a previous run must not
    # satisfy this run's acceptance evidence.
    before: set[str] = set()
    if journal.exists():
        with sqlite3.connect(journal) as db:
            before = {row[0] for row in db.execute("SELECT id FROM actions")}

    async def completed(name: str) -> None:
        async with asyncio.timeout(45):
            while True:
                with sqlite3.connect(journal) as db:
                    rows = [(json.loads(a), json.loads(b)) for a, b in db.execute("SELECT request,record FROM actions")]
                matching = [record for request, record in rows
                    if request["id"] not in before and (request.get("asset_id") or "").endswith("/" + name)]
                if any(record["state"] == "completed" for record in matching):
                    return
                if any(record["state"] in {"unknown", "failed", "cancelled"} for record in matching):
                    raise RuntimeError(f"simulation {name} did not complete; inspect the action journal")
                await asyncio.sleep(.05)

    verified = []
    try:
        await room.start()
        if provider.get("startup_target_revision"):
            await completed("wake_to_neutral")
            verified.append("wake_to_neutral")
        await completed("idle")
        verified.append("idle")
        room.observe("speaking", source="playback")
        await completed("speaking")
        verified.append("speaking")
        for gesture in ("yes", "no", "curious"):
            if gesture not in provider.get("choreography_revisions", {}):
                continue
            result = await room.express(gesture, refs[0]["id"])
            if result.get("outcome") != "accepted":
                raise RuntimeError(f"simulation {gesture} gesture was not accepted")
            await completed(gesture)
            verified.append(gesture)
    finally:
        shutdown = await room.close()
    if shutdown.get("outcome") != "acknowledged":
        raise RuntimeError("simulation shutdown was not confirmed")
    return {"outcome": "simulation_passed", "verified": verified,
            "shutdown": "acknowledged", "physical_motion_dispatched": False}


async def check(config_path: Path, command: str) -> dict:
    # Every acceptance run owns a new simulator and journal. Never clear an
    # earlier uncertain session's ownership records just to make a check pass.
    original = PuppeteerEnsembleConfig(config_path, command=command)
    original.preflight()
    room = json.loads(config_path.read_text())
    refs = room.get("robots", [])
    if room.get("schema_version") != 4 or len(refs) != 1 or refs[0].get("kind") != "soarm101":
        raise ValueError("check requires a SO-ARM-only v4 simulation room")
    robot_path = Path(refs[0]["file"])
    if not robot_path.is_absolute():
        robot_path = config_path.parent / robot_path
    robot = json.loads(robot_path.read_text())
    provider = robot["provider"]
    if provider.get("mode") != "sdk_simulation_daemon":
        raise ValueError("this check only opens an SDK simulation daemon")
    catalog = Path(provider["trusted_choreography_catalog"])
    provider["trusted_choreography_catalog"] = str(catalog if catalog.is_absolute() else (robot_path.parent / catalog).resolve())
    with tempfile.TemporaryDirectory(prefix="soarm-conversation-check-") as temporary:
        work = Path(temporary)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        provider.update(simulation_port=port, broker_url=f"http://127.0.0.1:{port}",
                        simulation_state_dir=str(work / "sdk-state"))
        room.update(action_journal=str(work / "actions.db"), ownership_db=str(work / "owners.db"))
        refs[0]["file"] = str(work / "robot.json")
        (work / "robot.json").write_text(json.dumps(robot))
        (work / "room.json").write_text(json.dumps(room))
        return await _check(work / "room.json", command)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--command", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(check(args.config.resolve(), args.command)), sort_keys=True))


if __name__ == "__main__":
    main()
