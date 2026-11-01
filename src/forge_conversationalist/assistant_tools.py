"""Optional LiveKit tools; capability enforcement belongs to the upstream owner.

No generic tool executor or robot driver is implemented in Conversationalist.
MCP filtering is defense-in-depth; the gateway must independently authenticate
each tool call and enforce its own grants.
"""

from __future__ import annotations

from .hermes import HermesRunsClient, HermesUnavailable
from .integrations import Integrations


def build_assistant_tools(
    integrations: Integrations,
    *,
    hermes_client: HermesRunsClient | None = None,
    mcp_module: object | None = None,
    function_tool_factory: object | None = None,
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
