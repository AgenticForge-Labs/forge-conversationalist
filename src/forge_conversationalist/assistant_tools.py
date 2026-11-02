"""Optional LiveKit tools; capability enforcement belongs to the upstream owner.

No generic tool executor or robot driver is implemented in Conversationalist.
MCP filtering is defense-in-depth; the gateway must independently authenticate
each tool call and enforce its own grants.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .embodiment import EmbodimentSession
    from .ensemble import EnsembleSession

from .hermes import HermesRunsClient, HermesUnavailable
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
) -> list[object]:
    """Construct tools for one room; no integrations means an empty list."""
    tools: list[object] = []

    if integrations.mcp is not None:
        if mcp_module is None:
            from livekit.agents import mcp as mcp_module
        conf = integrations.mcp
        server = mcp_module.MCPServerHTTP(
            conf.url,
            transport_type="streamable_http",
            headers={"Authorization": f"Bearer {conf.token}"},
            allowed_tools=list(conf.allowed_tools),
        )
        tools.append(mcp_module.MCPToolset(id="approved-mcp", mcp_server=server))

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

        async def request_ensemble_gesture(name: str) -> str:
            """Request a finite approved yes/no/acknowledge/neutral gesture across the room.

            Only when a person explicitly requests a physical gesture. Never
            infer success from accepted admission; no automatic retry.
            """
            return json.dumps(await ensemble_session.express(name))

        tools.extend([
            function_tool_factory(
                get_ensemble_status, name="get_ensemble_status",
                description="Read three-performer Puppeteer status and individual device evidence.",
            ),
            function_tool_factory(
                request_ensemble_gesture, name="request_ensemble_gesture",
                description=(
                    "On an explicit user request, request one approved yes/no/acknowledge/neutral "
                    "gesture for the three performers. Admission is not physical completion."
                ),
            ),
        ])

    if integrations.hermes is not None:
        if function_tool_factory is None:
            from livekit.agents import function_tool as function_tool_factory

        client = hermes_client or HermesRunsClient(integrations.hermes)

        async def delegate_to_hermes(task: str) -> str:
            """Delegate one substantive task to Hermes; returns a tracked run ID, not a success claim."""
            try:
                receipt = await client.submit(task)
            except (HermesUnavailable, ValueError):
                return "Hermes could not confirm admission. Do not retry automatically."
            return (
                f"Hermes accepted run {receipt['run_id']} with status {receipt['status']}. "
                "It is not necessarily complete. Use check_hermes_run to get the result."
            )

        async def check_hermes_run(run_id: str) -> str:
            """Check the actual progress or completion of a Hermes run started in this conversation."""
            try:
                state = await client.status(run_id)
            except (HermesUnavailable, ValueError):
                return "Unable to verify that Hermes run. Do not claim it succeeded."
            if state["status"] == "completed":
                return f"Hermes completed run {run_id}: {state.get('output', '')}"
            return f"Hermes run {run_id} has status {state['status']}. No completion is confirmed."

        tools.extend(
            [
                function_tool_factory(
                    delegate_to_hermes,
                    name="delegate_to_hermes",
                    description="Start an asynchronous Hermes task; return a run ID and do not claim completion.",
                ),
                function_tool_factory(
                    check_hermes_run,
                    name="check_hermes_run",
                    description="Check a Hermes task status or read the completed answer by run ID.",
                ),
            ]
        )
    return tools
