"""Read-only fake stdio server for native gain transport tests."""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import TypedDict

from mcp.server import MCPServer

data = json.loads(Path(sys.argv[sys.argv.index("--config") + 1]).read_text())
log = Path(data["test_log"])
reject = data.get("test_reject_native", False)
delay = data.get("test_native_delay_s", 0)
malformed = data.get("test_malformed_native", False)
app = MCPServer("fake-native-gain")
performers = {"reachy": {
    "status": "idle", "behavior_revision": "sha256:" + "a" * 64,
    "expressions": [], "presence_behaviors": ["idle", "listening", "speaking"],
}}
state = "disconnected"


class State(TypedDict):
    schema: str
    session_id: str | None
    state: str
    performers: dict[str, dict]


class Receipt(TypedDict):
    outcome: str
    state: str
    performers: dict[str, dict]


class GainReceipt(TypedDict, total=False):
    outcome: str
    frames_accepted: int
    stream_id: str
    last_sequence: int
    timing_source: str
    physical_motion_dispatched: bool


def record(kind, **fields):
    with log.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"kind": kind, "time": time.monotonic(), **fields}) + "\n")


@app.tool()
async def ensemble_state() -> State:
    return {"schema": "forge.behavior.v1", "session_id": None, "state": "disconnected",
            "performers": performers}


@app.tool()
async def ensemble_presence(session_id: str, event_id: str, sequence: int,
                            state: str, source: str = "agent_state",
                            interrupted: bool = False) -> Receipt:
    globals()["state"] = state
    record("presence", state=state, sequence=sequence, interrupted=interrupted)
    return {"outcome": "accepted", "state": state, "performers": performers}


@app.tool()
async def ensemble_expression(expression: str, action_id: str,
                              performer_ids: list[str] | None = None) -> GainReceipt:
    return {"outcome": "unsupported"}


@app.tool()
async def ensemble_native_speech_gain(session_id: str, performer_id: str,
                                      frames: list[dict[str, object]]) -> GainReceipt:
    record("gain", sequences=[frame["sequence"] for frame in frames], performer=performer_id,
           stream=frames[0]["stream_id"], keys=sorted(frames[0]))
    if delay:
        await asyncio.sleep(delay)
    if reject or state != "speaking":
        return {"outcome": "denied"}
    if malformed:
        return {"outcome": "observed"}
    return {"outcome": "observed", "frames_accepted": len(frames),
            "stream_id": str(frames[-1]["stream_id"]), "last_sequence": int(frames[-1]["sequence"]),
            "timing_source": "generated_pcm", "physical_motion_dispatched": False}


if __name__ == "__main__":
    app.run(transport="stdio")
