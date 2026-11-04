"""Hermes client tests use mocked HTTP only; no gateway or side effects."""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from forge_conversationalist.assistant_tools import build_assistant_tools
from forge_conversationalist.hermes import HermesRunsClient, HermesUnavailable
from forge_conversationalist.integrations import HermesConfig, Integrations


def _client(handler):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        return original(*args, transport=httpx.MockTransport(handler), **kwargs)

    return factory


def test_submit_uses_idempotency_and_isolated_api_owned_session() -> None:
    calls = []

    def fake_http(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(202, json={"run_id": "run_safe_123", "status": "started"})

    client = HermesRunsClient(HermesConfig(url="http://127.0.0.1:8642", key="secret"))
    with patch("forge_conversationalist.hermes.httpx.AsyncClient", _client(fake_http)):
        receipt = asyncio.run(client.submit("  inspect the offline test results  "))
    assert receipt == {"run_id": "run_safe_123", "status": "started"}
    assert calls[0].url.path == "/v1/runs"
    assert calls[0].headers["authorization"] == "Bearer secret"
    assert len(calls[0].headers["idempotency-key"]) >= 16
    body = json.loads(calls[0].content)
    assert body == {"input": "inspect the offline test results", "session_id": client.session_id}
    assert client.session_id.startswith("forge-voice-")
    assert "secret" not in repr(client)


def test_status_reports_completion_and_bounds_output() -> None:
    def fake_http(req):
        if req.method == "POST":
            return httpx.Response(202, json={"run_id": "run_42"})
        return httpx.Response(
            200, json={"run_id": "run_42", "status": "completed", "output": "ok " * 1000}
        )

    client = HermesRunsClient(HermesConfig(url="http://127.0.0.1:8642", key="secret"))
    with patch("forge_conversationalist.hermes.httpx.AsyncClient", _client(fake_http)):
        asyncio.run(client.submit("Review status"))
        status = asyncio.run(client.status("run_42"))
    assert status["status"] == "completed"
    assert status["truncated"] == "true"
    assert len(status["output"]) == 1600


@pytest.mark.parametrize("status", ["running", "waiting_for_approval", "cancelled", "unknown", "failed"])
def test_nonfinal_or_failed_runs_never_report_success(status: str) -> None:
    def fake_http(req):
        if req.method == "POST":
            return httpx.Response(202, json={"run_id": "run_42"})
        return httpx.Response(
            200, json={"run_id": "run_42", "status": status, "output": "misleading output"}
        )
    client = HermesRunsClient(HermesConfig(url="http://127.0.0.1:8642", key="secret"))
    with patch("forge_conversationalist.hermes.httpx.AsyncClient", _client(fake_http)):
        asyncio.run(client.submit("Review status"))
        result = asyncio.run(client.status("run_42"))
    assert result["status"] == status
    assert "output" not in result


def test_status_is_isolated_to_own_runs() -> None:
    client = HermesRunsClient(HermesConfig(url="http://127.0.0.1:8642", key="secret"))
    with pytest.raises(ValueError, match="Unknown run"):
        asyncio.run(client.status("run_someone_else"))


def test_submission_errors_are_not_retried_or_leaked() -> None:
    calls = []
    def fake_http(req):
        calls.append(req)
        return httpx.Response(503, text="secret upstream error")
    client = HermesRunsClient(HermesConfig(url="http://127.0.0.1:8642", key="secret"))
    with (
        patch("forge_conversationalist.hermes.httpx.AsyncClient", _client(fake_http)),
        pytest.raises(HermesUnavailable) as err,
    ):
        asyncio.run(client.submit("Do task"))
    assert len(calls) == 1
    assert "secret upstream error" not in str(err.value)


@pytest.mark.parametrize("bad", ["", "   ", "A" * 4001])
def test_input_is_bounded(bad: str) -> None:
    client = HermesRunsClient(HermesConfig(url="http://127.0.0.1:8642", key="secret"))
    with pytest.raises(ValueError):
        asyncio.run(client.submit(bad))


def test_hermes_livekit_tools_return_run_receipt_and_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONVERSATIONALIST_HERMES_STORE", str(tmp_path / "tasks.sqlite3"))
    class StubClient:
        async def submit(self, task):
            return {"run_id": "run_abc", "status": "started"}
        async def status(self, run_id):
            return {"run_id": run_id, "status": "completed", "output": "All tests passed."}

    factory = lambda fn, **kwargs: (kwargs["name"], fn)
    config = Integrations(hermes=HermesConfig(url="http://localhost:8642", key="secret"))
    tools = build_assistant_tools(config, hermes_client=StubClient(),
                                  function_tool_factory=factory)
    assert set(dict(tools)) == {"delegate_to_hermes", "check_hermes_run"}
    receipt = asyncio.run(dict(tools)["delegate_to_hermes"]("Check the build"))
    # The spoken receipt must not leak the internal run identifier.
    assert "run_abc" not in receipt
    assert "now working on" in receipt and "independently" in receipt
    result = asyncio.run(dict(tools)["check_hermes_run"]("build"))
    # The task is remembered by topic even before it finishes.
    assert "Check the build" in result
    assert "run_abc" not in result


def test_invalid_or_unknown_hermes_run_is_never_reported_as_completed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONVERSATIONALIST_HERMES_STORE", str(tmp_path / "tasks.sqlite3"))
    class StubClient:
        async def submit(self, task):
            raise HermesUnavailable("no")
        async def status(self, run_id):
            raise ValueError("unknown")
    factory = lambda fn, **kwargs: (kwargs["name"], fn)
    config = Integrations(hermes=HermesConfig(url="http://localhost:8642", key="secret"))
    tools = dict(build_assistant_tools(config, hermes_client=StubClient(),
                                      function_tool_factory=factory))
    assert "unavailable" in asyncio.run(tools["delegate_to_hermes"]("test"))
    assert "No Hermes task" in asyncio.run(tools["check_hermes_run"]("other"))


def test_completion_is_announced_without_polling_by_the_user(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONVERSATIONALIST_HERMES_STORE", str(tmp_path / "tasks.sqlite3"))
    """A finished run reaches the room as speech, not a silent receipt."""
    import forge_conversationalist.assistant_tools as tools_module

    monkeypatch.setattr(tools_module, "_HERMES_WATCH_INTERVAL_S", 0.01)
    monkeypatch.setattr(tools_module, "_HERMES_WATCH_TIMEOUT_S", 2.0)

    class Client:
        async def submit(self, task):
            return {"run_id": "run_done", "status": "started"}

        async def status(self, run_id):
            return {"run_id": run_id, "status": "completed", "output": "396"}

    announced: list[str] = []

    async def notifier(text: str) -> None:
        announced.append(text)

    def factory(function, *, name, description):
        return function

    built = build_assistant_tools(
        Integrations(hermes=HermesConfig(url="http://127.0.0.1:8642", key="k")),
        hermes_client=Client(),
        function_tool_factory=factory,
        hermes_completion_notifier=notifier,
    )
    delegate = next(t for t in built if t.__name__ == "delegate_to_hermes")

    async def scenario():
        reply = await delegate("compute 17*23+5")
        assert "run_done" not in reply and "now working on" in reply
        for _ in range(200):
            if announced:
                return reply
            await asyncio.sleep(0.01)
        raise AssertionError("completion was never announced")

    asyncio.run(scenario())
    assert "completed" in announced[0] and "396" in announced[0]


def test_task_store_remembers_results_by_topic(tmp_path) -> None:
    from forge_conversationalist.hermes_tasks import HermesTaskStore

    store = HermesTaskStore(tmp_path / "tasks.sqlite3", room="room-a")
    store.track("run_1", "what time is it")
    store.track("run_2", "look up the us election results")
    store.finish("run_1", "completed", "7:56 PM EST")
    assert [t.run_id for t in store.find("time")] == ["run_1"]
    assert store.find("time")[0].answer == "7:56 PM EST"
    assert [t.run_id for t in store.outstanding()] == ["run_2"]
    # Another room never sees this conversation's tasks.
    other = HermesTaskStore(tmp_path / "tasks.sqlite3", room="room-b")
    assert other.recent() == []
