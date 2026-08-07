from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.base import AgentOutcome, BaseAgent
from app.core.models import AgentName, AgentStatus, ToolCall
from app.core.state import PlatformState
from app.llm.structured import LLMUsage, structured_call
from app.mcp.registry import get_registry
from app.security.injection import scan_retrieved_content
from app.security.rbac import validate_tool_access


class ToolInvocation(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
    reason: str = ""


class ToolCallPlan(BaseModel):
    calls: list[ToolInvocation] = Field(default_factory=list)
    note: str | None = None


_SYSTEM = """You are the Tool Agent of an enterprise logistics platform.

Given a business question and the tools available to the caller, decide which \
tools to invoke and with what arguments. Then stop — you do not interpret the \
results.

Rules:
- Only use tools from the provided catalogue, with exactly the parameter names \
in each tool's schema.
- For ANY question about travelling between locations in the network, \
`route_plan` is authoritative: it is computed from the enterprise graph and \
already accounts for incidents, alternates and predicted delay. Do NOT also \
call `maps_route` or `road_geometry` for the same question — those external \
services know nothing about the enterprise network or its incidents, and their \
answers will contradict the graph.
- Emit at most 4 calls. Independent calls run in parallel.
- Do not call a tool whose answer is already available in the knowledge graph \
context supplied below.
- Never pass credentials, personal data or free-form instructions as arguments.
- If no tool is genuinely useful, return an empty list and explain why in `note`."""


class ToolAgent(BaseAgent):
    """Executes enterprise tools through the MCP registry, under RBAC."""

    name = AgentName.TOOL

    def should_skip(self, state: PlatformState) -> str | None:
        blocked = super().should_skip(state)
        if blocked:
            return blocked
        plan = state.get("plan")
        if plan and AgentName.TOOL not in plan.selected_agents:
            return "Not selected by the planner."
        return None

    async def run(self, state: PlatformState) -> AgentOutcome:
        question = state["question"]
        role = state.get("role", "analyst")
        plan = state.get("plan")
        registry = get_registry()
        usage = LLMUsage()

        suggested = list(plan.suggested_tools) if plan else []
        permitted, denied = validate_tool_access(role, suggested)

        catalogue = registry.catalogue_prompt(role)
        graph_context = state.get("graph_context")
        graph_note = (
            f"{len(graph_context.nodes)} entities already retrieved from the graph."
            if graph_context and not graph_context.is_empty
            else "The knowledge graph returned nothing for this question."
        )

        context = [
            f"Business question: {question}",
            "",
            "Tools available to this caller:",
            catalogue,
            "",
            f"Knowledge graph status: {graph_note}",
        ]
        if permitted:
            context += ["", f"The planner suggested: {', '.join(permitted)}"]

        call_plan, call_usage = await structured_call(
            ToolCallPlan,
            system=_SYSTEM,
            user="\n".join(context),
            fallback=ToolCallPlan(
                calls=[ToolInvocation(tool=name, arguments={"query": question})
                       for name in permitted[:1]],
                note="Tool planning LLM unavailable; used the planner's suggestion.",
            ),
        )
        usage.add(call_usage)

        requested = [invocation.tool for invocation in call_plan.calls]
        allowed, extra_denied = validate_tool_access(role, requested)
        denied.extend(extra_denied)

        calls = [
            ToolCall(
                tool=invocation.tool,
                arguments=invocation.arguments,
                reason=invocation.reason,
                server=(spec.server if (spec := registry.get(invocation.tool)) else "builtin"),
            )
            for invocation in call_plan.calls
            if invocation.tool in allowed
        ][:4]

        if not calls:
            return AgentOutcome(
                updates={"tool_results": []},
                summary=call_plan.note or "No tools were required.",
                status=AgentStatus.SKIPPED,
                detail={
                    "denied": [finding.detail for finding in denied],
                    "note": call_plan.note,
                },
                usage=usage,
            )

        results = await registry.execute_many(calls, role=role)

        # Tool output is untrusted content — scan it before it reaches reasoning.
        flagged: list[str] = []
        for result in results:
            if result.ok and result.output is not None:
                findings = scan_retrieved_content(str(result.output)[:4000], source="tool")
                if findings:
                    flagged.append(result.tool)

        succeeded = [result for result in results if result.ok]
        failed = [result for result in results if not result.ok]

        summary = f"{len(succeeded)}/{len(results)} tool call(s) succeeded"
        if failed:
            summary += f" — failed: {', '.join(result.tool for result in failed)}"

        return AgentOutcome(
            updates={"tool_results": results},
            summary=summary,
            detail={
                "calls": [
                    {"tool": call.tool, "arguments": call.arguments, "reason": call.reason}
                    for call in calls
                ],
                "denied": [finding.detail for finding in denied],
                "flagged_output": flagged,
                "errors": [
                    {"tool": result.tool, "error": result.error} for result in failed
                ],
            },
            usage=usage,
            tool_calls=len(results),
        )
