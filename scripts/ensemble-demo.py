"""Run one room-owned Conversationalist -> Puppeteer fake MCP demo.

This requires the sibling forge-puppeteer checkout. It starts Puppeteer's own
MCP ensemble fixture, whose providers are in-memory fakes, then exercises the
same Conversationalist room client used by the LiveKit worker. It opens no
LiveKit room, SDK, serial port, camera, or robot connection.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from forge_conversationalist.ensemble import EnsembleSession, PuppeteerEnsembleConfig


def _demo_config(root: Path, work: Path) -> tuple[Path, Path]:
    puppeteer_src = root / "src"
    fixture = root / "tests/robots/mcp_fake_ensemble_server.py"
    if not fixture.is_file():
        raise FileNotFoundError(f"Puppeteer fake MCP fixture not found: {fixture}")

    # Puppeteer requires an executable with its production entrypoint name.
    # This wrapper invokes only Puppeteer's source-controlled fake fixture.
    command = work / "forge-puppeteer-ensemble"
    command.write_text(
        f"#!{sys.executable}\n"
        "import runpy, sys\n"
        f"sys.path.insert(0, {str(puppeteer_src)!r})\n"
        f"runpy.run_path({str(fixture)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    command.chmod(0o700)

    neutral = work / "neutral.json"
    neutral.write_text(json.dumps([
        [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1],
    ]), encoding="utf-8")
    config = work / "ensemble.json"
    config.write_text(json.dumps({
        "schema_version": 1,
        "motion_authority": "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION",
        "reachy": {
            "backend": "reachy_sdk", "device": "reachy", "instance_id": "reachy",
            "physical_authority": "I_AUTHORIZE_REACHY_CONTROL",
            "allowed_tools": ["head_pose", "play_move", "device_stop"],
        },
        "stackchan": {
            "backend": "stackchan_sdk", "device": "stackchan", "instance_id": "stackchan",
            "physical_authority": "I_AUTHORIZE_STACKCHAN_CONTROL",
            "allowed_tools": ["head_move", "face_update", "device_stop"],
        },
        "soarm101": {"instance_id": "soarm101", "mode": "simulation"},
        "action_journal": str(work / "actions.sqlite3"),
        "ownership_db": str(work / "owners.sqlite3"),
        "reachy_neutral_head": str(neutral),
    }), encoding="utf-8")
    return command, config


async def run_demo(puppeteer_root: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="forge-ensemble-demo-") as temporary:
        work = Path(temporary)
        command, path = _demo_config(puppeteer_root, work)
        config = PuppeteerEnsembleConfig(path, command=str(command))
        config.preflight()
        client = EnsembleSession(config, room_id="offline-demo-room", caller_id="demo-owner")
        # EnsembleSession passes env={} to stdio_client, so provider
        # credentials cannot cross the child-process seam.
        try:
            startup = await client.start()
            client.observe("listening")
            client.observe("speaking")
            gesture = await client.express("yes", "reachy")
            client.observe("listening", interrupted=True)
            await asyncio.sleep(0.15)
            status = await client.status()
            shutdown = await client.close()
            if set(startup.get("performers", {})) != {"reachy", "stackchan", "soarm101"}:
                raise RuntimeError("Puppeteer returned an unexpected performer catalog")
            if gesture.get("outcome") != "accepted":
                raise RuntimeError(f"Puppeteer fake gesture was not accepted: {gesture.get('outcome')}")
            if status.get("schema") != "forge.behavior.v1":
                raise RuntimeError("Puppeteer returned an invalid behavior snapshot")
            return {
                "outcome": "simulation_passed",
                "performers": list(startup["performers"]),
                "gesture_outcome": gesture["outcome"],
                "room_shutdown": shutdown.get("outcome"),
                "software_status_before_close": status.get("state"),
                "physical_motion_dispatched": False,
                "livekit_connected": False,
            }
        finally:
            if not client._closed:
                await client.close()


def main() -> int:
    default_root = Path(__file__).resolve().parents[2] / "forge-puppeteer"
    root = Path(os.environ.get("PUPPETEER_SOURCE_ROOT", default_root)).resolve()
    if not root.is_dir():
        print(f"Puppeteer source checkout not found: {root}", file=sys.stderr)
        return 2
    try:
        report = asyncio.run(run_demo(root))
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(json.dumps({"outcome": "simulation_failed", "reason": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
