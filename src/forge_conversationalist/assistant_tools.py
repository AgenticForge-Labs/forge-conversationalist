"""Optional LiveKit tools; capability enforcement belongs to the upstream owner.

No generic tool executor or robot driver is implemented in Conversationalist.
MCP filtering is defense-in-depth; the gateway must independently authenticate
each tool call and enforce its own grants.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .embodiment import EmbodimentSession
    from .ensemble import EnsembleSession

from .hermes import HermesRunsClient, HermesUnavailable
from .hermes_tasks import HermesTaskStore

logger = logging.getLogger(__name__)
_HERMES_WATCH_TIMEOUT_S = 900.0
_HERMES_WATCH_INTERVAL_S = 2.5
from .integrations import Integrations


def _restricted_mcp_toolset(mcp_module: object, server: object, allowed: tuple[str, ...]):
    """Create a toolset that filters after discovery and before agent exposure."""
    parent = mcp_module.MCPToolset

    class RestrictedMCPToolset(parent):
        async def setup(self, *, reload: bool = False):
            await super().setup(reload=reload)
            # Current LiveKit wraps these tools in RawFunctionTool, while its
            # filter_tools helper checks for MCPTool. Unknown wrappers are
            # excluded fail-closed instead of losing the allowlist.
            self._tools = [
                tool for tool in self._tools
                if getattr(tool, "name", None) in allowed
                or getattr(getattr(tool, "_info", None), "name", None) in allowed
            ]
            return self

    return RestrictedMCPToolset(id="approved-puppeteer", mcp_server=server)


def build_assistant_tools(
    integrations: Integrations,
    *,
    hermes_client: HermesRunsClient | None = None,
    mcp_module: object | None = None,
    function_tool_factory: object | None = None,
    embodiment_session: EmbodimentSession | None = None,
    ensemble_session: EnsembleSession | None = None,
    hermes_completion_notifier: Callable[[str], Awaitable[None]] | None = None,
    hermes_room: str = "room",
) -> list[object]:
    """Construct tools for one room; no integrations means an empty list."""
    tools: list[object] = []

    if integrations.mcp is not None:
        if mcp_module is None:
            from livekit.agents import mcp as mcp_module
        conf = integrations.mcp
        server = mcp_module.MCPServerHTTP(
            conf.url,
            transport_type=conf.transport,
            headers={"Authorization": f"Bearer {conf.token}"},
            allowed_tools=list(conf.allowed_tools),
            timeout=conf.timeout,
        )
        tools.append(mcp_module.MCPToolset(id="approved-mcp", mcp_server=server))

    if integrations.company_services is not None:
        if mcp_module is None:
            from livekit.agents import mcp as mcp_module
        conf = integrations.company_services
        # Read-only Platform company services. The child reads its own private
        # env file, so the worker forwards no provider credentials.
        server = mcp_module.MCPServerStdio(
            command=conf.command,
            args=conf.args(),
            env={},
        )
        tools.append(_restricted_mcp_toolset(mcp_module, server, conf.allowed_tools))

    if integrations.puppeteer_stdio is not None:
        if mcp_module is None:
            from livekit.agents import mcp as mcp_module
        conf = integrations.puppeteer_stdio
        # The SDK's default child environment inherits only HOME, LOGNAME, PATH,
        # SHELL, TERM and USER. Never forward this worker's provider credentials.
        server = mcp_module.MCPServerStdio(
            command=conf.command,
            args=["--config", str(conf.config_path)],
            env={},
        )
        tools.append(_restricted_mcp_toolset(mcp_module, server, conf.allowed_tools))

    if integrations.embodiment is not None and embodiment_session is not None:
        if function_tool_factory is None:
            from livekit.agents import function_tool as function_tool_factory

        async def discover_performer() -> str:
            """Discover the configured fake performer, available expressions and their revisions."""
            return json.dumps(await embodiment_session.discover())

        async def request_performer_expression(kind: str, name: str, asset_revision: str) -> str:
            """Request a named simulated emotion, dance or gesture after discovery.

            The exact listed kind, name and asset revision are required. This is
            not a physical motion capability or proof of action completion.
            """
            return json.dumps(await embodiment_session.express(kind, name, asset_revision))

        async def check_performer_action(action_id: str) -> str:
            """Check an action ID after acceptance; never assume queued is completed."""
            return json.dumps(await embodiment_session.lookup(action_id))

        tools.extend([
            function_tool_factory(discover_performer, name="discover_performer",
                                  description="Discover fake robot expression names, asset pins, and limitations."),
            function_tool_factory(request_performer_expression,
                                  name="request_performer_expression",
                                  description="Request one exact simulated emotion, dance or gesture. No random fallback or hardware motion."),
            function_tool_factory(check_performer_action, name="check_performer_action",
                                  description="Read confirmed status of a previous performer expression action ID."),
        ])

    if integrations.ensemble is not None and ensemble_session is not None:
        if function_tool_factory is None:
            from livekit.agents import function_tool as function_tool_factory

        async def get_ensemble_status() -> str:
            """Read Puppeteer's observed room/performer states; no physical success inferred."""
            return json.dumps(await ensemble_session.status())

        async def request_ensemble_gesture(name: str, performer_id: str) -> str:
            """Request one finite catalogued gesture from a selected performer.

            First inspect get_ensemble_status for that performer's approved
            expressions. A person must explicitly request the gesture.
            Never infer motion completion or automatically retry.
            """
            result = await ensemble_session.express(name, performer_id)
            outcome = result.get("outcome")
            if outcome == "accepted":
                result = {
                    **result,
                    "completion": "unconfirmed",
                    "spoken_guidance": (
                        f"{name} was requested for {performer_id}. It is not "
                        "confirmed complete; say it was requested, never that "
                        "it happened."
                    ),
                }
            elif outcome == "unsupported":
                # Say plainly that the gesture does not exist here; do not
                # imply a request was made.
                result = {
                    **result,
                    "spoken_guidance": (
                        f"{performer_id} does not offer {name}. Tell the user "
                        "that gesture is unavailable there and offer one from "
                        "get_ensemble_status instead."
                    ),
                }
            elif outcome == "denied":
                result = {
                    **result,
                    "spoken_guidance": (
                        f"{performer_id} is unavailable right now ({result.get('reason')}). "
                        "Report that, and do not retry automatically."
                    ),
                }
            return json.dumps(result)

        tools.extend([
            function_tool_factory(
                get_ensemble_status, name="get_ensemble_status",
                description="Read Puppeteer room status and individual configured performer evidence.",
            ),
            function_tool_factory(
                request_ensemble_gesture, name="request_ensemble_gesture",
                description=(
                    "On an explicit user request, inspect get_ensemble_status and request an "
                    "exact approved named gesture for one selected performer_id. "
                    "Do not issue a gesture to every robot by default. Admission is not physical completion; "
                    "never say it happened unless its completion has been independently confirmed."
                ),
            ),
        ])

    if integrations.hermes is not None:
        if function_tool_factory is None:
            from livekit.agents import function_tool as function_tool_factory

        client = hermes_client or HermesRunsClient(integrations.hermes)
        tasks = HermesTaskStore(room=hermes_room)

        def task_with_context(task: str) -> str:
            """Give Hermes the room's local timezone without exposing anything else."""
            import datetime as _dt
            now = _dt.datetime.now().astimezone()
            stamp = now.strftime("%Y-%m-%d %H:%M %Z (UTC%z)")
            return f"{task}\n\nRoom context: local time is {stamp}."

        async def watch_run(run_id: str) -> None:
            """Announce a terminal run once, without the user asking for status."""
            task_text = next(
                (t.task for t in tasks.recent(20) if t.run_id == run_id), "your task",
            )
            deadline = time.monotonic() + _HERMES_WATCH_TIMEOUT_S
            while time.monotonic() < deadline:
                await asyncio.sleep(_HERMES_WATCH_INTERVAL_S)
                try:
                    state = await client.status(run_id)
                except (HermesUnavailable, ValueError):
                    continue
                status = state.get("status")
                if status not in {"completed", "failed", "cancelled", "interrupted"}:
                    continue
                answer = str(state.get("output", "")).strip()
                tasks.finish(run_id, status, answer)
                if status == "completed":
                    short = answer
                    if len(short) > 400:
                        short = short[:400].rstrip() + "... Ask if you want the details."
                    report = f"for \"{task_text}\" completed. Answer: {short}"
                else:
                    report = f"for \"{task_text}\" ended with status {status}."
                try:
                    await hermes_completion_notifier(f"Hermes {report}")
                except Exception:
                    logger.exception("Hermes completion announcement failed")
                return

        async def delegate_to_hermes(task: str) -> str:
            """Hand any task the assistant cannot do itself to Hermes."""
            try:
                receipt = await client.submit(task_with_context(task))
            except (HermesUnavailable, ValueError):
                return "Hermes is unavailable right now, so that task did not start."
            tasks.track(receipt["run_id"], task)
            if hermes_completion_notifier is not None:
                asyncio.create_task(watch_run(receipt["run_id"]))
            return (
                f"Hermes is now working on \"{task[:120]}\". Tasks run "
                "independently, so keep asking new questions; each answer is "
                "announced when it arrives."
            )

        async def check_hermes_run(topic: str = "") -> str:
            """Answer a follow-up about a task, by topic when one is given."""
            topic = topic.strip()
            matches = tasks.find(topic) if topic else []
            if not matches:
                matches = tasks.outstanding()[:3] or tasks.recent(3)
            if not matches:
                return "No Hermes task has been started in this conversation."
            lines = []
            for item in matches:
                if item.status == "completed":
                    lines.append(f"Done: \"{item.task}\" -> {item.answer[:500]}")
                else:
                    lines.append(f"Still {item.status}: \"{item.task}\"")
            return " | ".join(lines)

        tools.extend(
            [
                function_tool_factory(
                    delegate_to_hermes,
                    name="delegate_to_hermes",
                    description=(
                        "Use this immediately for anything you cannot answer or do "
                        "yourself: current date/time, live facts, web lookups, research, "
                        "coding, calculations beyond trivial arithmetic. Never say you "
                        "lack access to the time or to live information; delegate it. "
                        "Company services live behind Hermes: company-mail "
                        "(read-only, content allowed) and company-calendar (read-only) "
                        "via service_connection_read. Delegate email and calendar "
                        "questions there. Hermes runs several tasks at once, so a new question is sent "
                        "even while another is still running; never wait for a pending "
                        "task before starting the next one. Do not ask for permission "
                        "and do not claim an answer before it arrives."
                    ),
                ),
                function_tool_factory(
                    check_hermes_run,
                    name="check_hermes_run",
                    description=(
                        "Use for any follow-up about earlier Hermes work. Pass the "
                        "topic the user asked about (for example 'time' or 'election "
                        "results') to get that task's real status and answer. Omit the "
                        "topic to summarize outstanding and recent tasks."
                    ),
                ),
            ]
        )
    return tools
