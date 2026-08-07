from __future__ import annotations

from app.agents.base import AgentOutcome, BaseAgent
from app.core.models import AgentName, ExecutionPlan, Intent, PlanStep
from app.core.state import PlatformState
from app.domain.constraints import constraint_catalogue
from app.kg.introspect import get_graph_schema
from app.kg.ontology import ontology_for_domain
from app.llm.structured import structured_call
from app.mcp.registry import get_registry
from app.security.rbac import allowed_tools_for


_SYSTEM = """You are the Planner Agent of an enterprise multi-agent platform \
operating in the {domain} domain.

Your job is to turn a business question into an execution plan. You do not \
answer the question yourself.

Available worker agents:
- knowledge   : Neo4j knowledge graph — entity discovery, relationship traversal, \
multi-hop reasoning, dependency and impact analysis, historical context.
- search      : hybrid search over the graph, enterprise documents and the public web.
- tool        : executes MCP tools (weather, routing, SQL, REST, filesystem).
- reasoning   : root-cause analysis, business insight, recommendations.
- optimization: ranks feasible options against HARD logistics constraints.
- validation  : fact and constraint verification (always runs, never plan it).

Planning rules:
1. Always include `knowledge` unless the question is purely about external \
public information.
2. Include `search` when policies, documents, SOPs or external advisories matter.
3. Include `tool` only when a listed tool genuinely adds information the graph \
cannot supply — name those tools in `suggested_tools`.
4. Include `optimization` whenever the user asks for a best/optimal/cheapest/ \
fastest option, a route, an allocation, a schedule or a reassignment. Any such \
answer MUST be constraint-checked.
5. `reasoning` is required whenever the answer needs analysis rather than lookup.
6. Steps that do not depend on one another share a `parallel_group` so they run \
concurrently. Context gathering (knowledge, search, tool) is normally group 0; \
analysis is group 1; optimisation is group 2.
7. Keep the plan minimal — every extra step costs latency and tokens.

Set `intent.ambiguous` to true and supply a `clarifying_question` only when the \
question cannot be acted on at all without more input.

These hard constraints govern any operational recommendation:
{constraints}"""


class PlannerAgent(BaseAgent):
    """Intent analysis, task decomposition, agent selection and parallel planning."""

    name = AgentName.PLANNER

    def should_skip(self, state: PlatformState) -> str | None:
        # Planning happens before security clearance in the graph, but a replan
        # after a block would be wasted work.
        if state.get("blocked"):
            return "Request was blocked by the Security Agent."
        return None

    async def run(self, state: PlatformState) -> AgentOutcome:
        question = state["question"]
        role = state.get("role", "analyst")
        ontology = ontology_for_domain()
        schema = await get_graph_schema()
        registry = get_registry()

        allowed = set(allowed_tools_for(role))
        catalogue = "\n".join(
            spec.to_prompt_line()
            for spec in registry.specs()
            if spec.name in allowed
        ) or "(no tools available to this role)"

        context = [
            f"Business question: {question}",
            "",
            f"Caller role: {role}",
            "",
            f"Knowledge graph schema (source: {schema.source}):",
            schema.to_prompt(),
            "",
            "Tools available to this role:",
            catalogue,
            "",
            f"Typical questions in this domain: {'; '.join(ontology.key_questions[:4])}",
        ]

        notes = state.get("reflection_notes") or []
        if notes:
            context += [
                "",
                "The previous attempt was rejected. Address this feedback:",
                *(f"- {note}" for note in notes),
            ]

        history = state.get("history") or []
        if history:
            context += ["", "Recent conversation is provided as prior turns."]

        fallback = _default_plan(question)
        plan, usage = await structured_call(
            ExecutionPlan,
            system=_SYSTEM.format(
                domain=ontology.domain, constraints=constraint_catalogue()
            ),
            user="\n".join(context),
            history=history[-6:],
            fallback=fallback,
        )

        plan = _sanitise(plan, allowed_tools=allowed)

        summary = (
            f"Intent '{plan.intent.category}' -> "
            f"{len(plan.steps)} step(s) across "
            f"{len({step.parallel_group for step in plan.steps})} stage(s): "
            f"{', '.join(agent.value for agent in plan.selected_agents)}"
        )

        return AgentOutcome(
            updates={"plan": plan},
            summary=summary,
            detail={
                "intent": plan.intent.model_dump(mode="json"),
                "agents": [agent.value for agent in plan.selected_agents],
                "suggested_tools": plan.suggested_tools,
                "strategy": plan.strategy_note,
            },
            usage=usage,
        )


def _default_plan(question: str) -> ExecutionPlan:
    """Used when the LLM is unavailable — a safe, generally useful plan."""

    return ExecutionPlan(
        intent=Intent(summary=question[:300], category="general"),
        steps=[
            PlanStep(
                id="s1",
                agent=AgentName.KNOWLEDGE,
                objective="Retrieve related entities and relationships from the graph.",
                parallel_group=0,
            ),
            PlanStep(
                id="s2",
                agent=AgentName.SEARCH,
                objective="Find supporting documents and external context.",
                parallel_group=0,
            ),
            PlanStep(
                id="s3",
                agent=AgentName.REASONING,
                objective="Analyse the gathered context and answer the question.",
                depends_on=["s1", "s2"],
                parallel_group=1,
            ),
        ],
        selected_agents=[AgentName.KNOWLEDGE, AgentName.SEARCH, AgentName.REASONING],
        strategy_note="Fallback plan — planner LLM unavailable.",
    )


#: Agents the planner may schedule. Validation, reflection, explanation,
#: observability and self-improvement are always-on parts of the pipeline.
_PLANNABLE = {
    AgentName.KNOWLEDGE,
    AgentName.SEARCH,
    AgentName.TOOL,
    AgentName.REASONING,
    AgentName.OPTIMIZATION,
}


def _sanitise(plan: ExecutionPlan, *, allowed_tools: set[str]) -> ExecutionPlan:
    """Drop steps the platform does not schedule and tools the role cannot use."""

    steps = [step for step in plan.steps if step.agent in _PLANNABLE]

    # A tool step with no permitted tool is pointless.
    tools = [tool for tool in plan.suggested_tools if tool in allowed_tools]
    if not tools:
        steps = [step for step in steps if step.agent is not AgentName.TOOL]

    if not steps:
        steps = _default_plan(plan.intent.summary).steps

    # Reasoning is mandatory whenever anything was gathered.
    agents = {step.agent for step in steps}
    if AgentName.REASONING not in agents:
        steps.append(
            PlanStep(
                id="auto-reasoning",
                agent=AgentName.REASONING,
                objective="Analyse gathered context and answer the question.",
                depends_on=[step.id for step in steps],
                parallel_group=max(step.parallel_group for step in steps) + 1,
            )
        )
        agents.add(AgentName.REASONING)

    ordered = [agent for agent in AgentName if agent in agents]
    return ExecutionPlan(
        intent=plan.intent,
        steps=steps,
        selected_agents=ordered,
        suggested_tools=tools,
        strategy_note=plan.strategy_note,
    )
