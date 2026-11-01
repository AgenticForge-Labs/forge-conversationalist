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


def test_hermes_livekit_tools_return_run_receipt_and_status() -> None:
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
    assert "accepted run run_abc" in receipt
    assert "not necessarily complete" in receipt
    result = asyncio.run(dict(tools)["check_hermes_run"]("run_abc"))
    assert "All tests passed" in result


def test_invalid_or_unknown_hermes_run_is_never_reported_as_completed() -> None:
    class StubClient:
        async def submit(self, task):
            raise HermesUnavailable("no")
        async def status(self, run_id):
            raise ValueError("unknown")
    factory = lambda fn, **kwargs: (kwargs["name"], fn)
    config = Integrations(hermes=HermesConfig(url="http://localhost:8642", key="secret"))
    tools = dict(build_assistant_tools(config, hermes_client=StubClient(),
                                      function_tool_factory=factory))
    assert "could not confirm" in asyncio.run(tools["delegate_to_hermes"]("test"))
    assert "Do not claim" in asyncio.run(tools["check_hermes_run"]("other"))
