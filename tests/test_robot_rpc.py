"""Owner-only robot controls; all tests use a synthetic ensemble, never hardware."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from forge_conversationalist.robot_rpc import register_robot_rpc


class StubEnsemble:
    def __init__(self):
        self.requests = []

    async def status(self):
        return {
            "schema": "forge.behavior.v1",
            "state": "listening",
            "performers": {
                "reachy": {
                    "status": "idle",
                    "behavior_revision": "sha256:" + "a" * 64,
                    "expressions": ["yes", "no", "happy"],
                    "presence_behaviors": ["idle", "listening", "speaking"],
                },
                "stackchan": {"status": "unavailable", "expressions": ["yes"]},
            },
        }

    async def express(self, expression, performer_id):
        self.requests.append((expression, performer_id))
        return {"outcome": "accepted", "action_id": "test-action"}


def handlers(ensemble=None):
    result = {}
    robot = ensemble or StubEnsemble()
    register_robot_rpc(
        SimpleNamespace(register_rpc_method=lambda name, method: result.__setitem__(name, method)),
        ensemble=robot,
        authorized_identity="operator",
        room=SimpleNamespace(remote_participants={"operator": object()}),
        rpc_error_factory=ValueError,
    )
    return robot, result


def test_robot_rpc_read_and_single_device_gesture():
    stub, methods = handlers()

    async def run():
        state = json.loads(await methods["forge.get_robots"](
            SimpleNamespace(caller_identity="operator", payload="{}")
        ))
        assert state["performers"]["reachy"]["expressions"] == ["yes", "no", "happy"]
        assert state["performers"]["stackchan"]["status"] == "unavailable"
        outcome = json.loads(await methods["forge.request_robot_expression"](
            SimpleNamespace(
                caller_identity="operator",
                payload=json.dumps({"performer_id": "reachy", "expression": "happy"}),
            )
        ))
        assert outcome["outcome"] == "accepted"
        assert outcome["completion"] == "unconfirmed"

    asyncio.run(run())
    assert stub.requests == [("happy", "reachy")]


@pytest.mark.parametrize("caller,payload", [
    ("guest", '{"performer_id":"reachy","expression":"yes"}'),
    ("operator", '{"performer_id":"reachy","expression":"yes","joints":[0]}'),
    ("operator", '{"performer_id":"reachy","expression":3}'),
    ("operator", '{"performer_id":"reachy","expression":"bad name"}'),
    ("operator", "not-json"),
    ("operator", "{}"),
    ("operator", "x" * 600),
])
def test_robot_rpc_rejects_unauthorized_or_untyped_requests(caller, payload):
    robot, methods = handlers()

    async def run():
        with pytest.raises(ValueError):
            await methods["forge.request_robot_expression"](
                SimpleNamespace(caller_identity=caller, payload=payload)
            )
        if caller == "guest":
            with pytest.raises(ValueError):
                await methods["forge.get_robots"](
                    SimpleNamespace(caller_identity=caller, payload="{}")
                )

    asyncio.run(run())
    assert robot.requests == []
