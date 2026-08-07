from __future__ import annotations

from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents import (
    ExplanationAgent,
    GuardrailAgent,
    KnowledgeAgent,
    ObservabilityAgent,
    OptimizationAgent,
    PlannerAgent,
    ReasoningAgent,
    ReflectionAgent,
    SearchAgent,
    SecurityAgent,
    SelfImprovingAgent,
    ToolAgent,
    ValidationAgent,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.core.models import AgentName
from app.core.state import PlatformState


logger = get_logger(__name__)


#: Node identifiers, kept in sync with :class:`AgentName` so the UI timeline and
#: the graph topology use the same vocabulary.
GUARDRAIL = AgentName.GUARDRAIL.value
SECURITY = AgentName.SECURITY.value
PLANNER = AgentName.PLANNER.value
KNOWLEDGE = AgentName.KNOWLEDGE.value
SEARCH = AgentName.SEARCH.value
TOOL = AgentName.TOOL.value
REASONING = AgentName.REASONING.value
OPTIMIZATION = AgentName.OPTIMIZATION.value
VALIDATION = AgentName.VALIDATION.value
REFLECTION = AgentName.REFLECTION.value
EXPLANATION = AgentName.EXPLANATION.value
OBSERVABILITY = AgentName.OBSERVABILITY.value
SELF_IMPROVING = AgentName.SELF_IMPROVING.value

#: Context-gathering agents that fan out in parallel after planning.
CONTEXT_AGENTS = (KNOWLEDGE, SEARCH, TOOL)


# ----------------------------------------------------------------------
# Routing
# ----------------------------------------------------------------------
def route_after_guardrail(state: PlatformState) -> str:
    """A blocked request by Guardrail Agent skips straight to observability."""
    return OBSERVABILITY if state.get("blocked") else SECURITY


def route_after_security(state: PlatformState) -> str:
    """A blocked request skips straight to observability and returns."""

    return OBSERVABILITY if state.get("blocked") else PLANNER


def route_after_planner(state: PlatformState) -> list[str]:
    """Fan out to whichever context agents the plan selected.

    Returning a list makes LangGraph execute these nodes concurrently; they all
    converge on ``reasoning``, which waits for every branch.
    """

    plan = state.get("plan")
    if plan is None:
        return [KNOWLEDGE, SEARCH]

    selected = {agent.value for agent in plan.selected_agents}
    branches = [name for name in CONTEXT_AGENTS if name in selected]
    return branches or [KNOWLEDGE]


def route_after_reasoning(state: PlatformState) -> str:
    """Optimisation runs only when the question calls for a chosen option."""

    plan = state.get("plan")
    wanted = plan is not None and AgentName.OPTIMIZATION in plan.selected_agents
    if wanted and settings.workflow_enable_optimization:
        return OPTIMIZATION
    return VALIDATION


def route_after_reflection(state: PlatformState) -> list[str] | str:
    """Close the reflection loop, or proceed to response generation."""

    verdict = state.get("reflection")
    if verdict is None or not verdict.should_retry:
        return EXPLANATION

    if state.get("reflection_loops", 0) > settings.workflow_max_reflection_loops:
        logger.warning("Reflection loop budget exceeded; forcing response")
        return EXPLANATION

    targets = {agent.value for agent in verdict.retry_agents}
    branches = [name for name in CONTEXT_AGENTS if name in targets]
    if branches:
        return branches
    # Re-running analysis alone is the common case.
    return REASONING


# ----------------------------------------------------------------------
# Assembly
# ----------------------------------------------------------------------
def build_workflow(*, checkpointer: MemorySaver | None = None):
    """Compile the enterprise workflow described in the platform spec.

    Order note: the guardrail and security gates run *before* the planner, so no untrusted
    input reaches an LLM call, and PII is redacted before planning. Tool-level
    permission checks still happen at execution time inside the Tool Agent.
    """

    graph = StateGraph(PlatformState)

    graph.add_node(GUARDRAIL, GuardrailAgent())
    graph.add_node(SECURITY, SecurityAgent())
    graph.add_node(PLANNER, PlannerAgent())
    graph.add_node(KNOWLEDGE, KnowledgeAgent())
    graph.add_node(SEARCH, SearchAgent())
    graph.add_node(TOOL, ToolAgent())
    graph.add_node(REASONING, ReasoningAgent())
    graph.add_node(OPTIMIZATION, OptimizationAgent())
    graph.add_node(VALIDATION, ValidationAgent())
    graph.add_node(REFLECTION, ReflectionAgent())
    graph.add_node(EXPLANATION, ExplanationAgent())
    graph.add_node(OBSERVABILITY, ObservabilityAgent())
    graph.add_node(SELF_IMPROVING, SelfImprovingAgent())

    graph.add_edge(START, GUARDRAIL)
    graph.add_conditional_edges(
        GUARDRAIL,
        route_after_guardrail,
        {SECURITY: SECURITY, OBSERVABILITY: OBSERVABILITY},
    )
    graph.add_conditional_edges(
        SECURITY,
        route_after_security,
        {PLANNER: PLANNER, OBSERVABILITY: OBSERVABILITY},
    )

    graph.add_conditional_edges(
        PLANNER,
        route_after_planner,
        {name: name for name in CONTEXT_AGENTS},
    )

    # Context gathering converges on reasoning.
    for name in CONTEXT_AGENTS:
        graph.add_edge(name, REASONING)

    graph.add_conditional_edges(
        REASONING,
        route_after_reasoning,
        {OPTIMIZATION: OPTIMIZATION, VALIDATION: VALIDATION},
    )
    graph.add_edge(OPTIMIZATION, VALIDATION)
    graph.add_edge(VALIDATION, REFLECTION)

    graph.add_conditional_edges(
        REFLECTION,
        route_after_reflection,
        {
            EXPLANATION: EXPLANATION,
            REASONING: REASONING,
            **{name: name for name in CONTEXT_AGENTS},
        },
    )

    graph.add_edge(EXPLANATION, OBSERVABILITY)
    graph.add_edge(OBSERVABILITY, SELF_IMPROVING)
    graph.add_edge(SELF_IMPROVING, END)

    interrupt_before = [TOOL] if settings.workflow_human_in_the_loop else None
    if interrupt_before and checkpointer is None:
        # Interrupts require persistence to resume from.
        checkpointer = MemorySaver()

    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
    )
    logger.info(
        "Workflow compiled",
        extra={
            "nodes": 13,
            "human_in_the_loop": settings.workflow_human_in_the_loop,
            "max_reflection_loops": settings.workflow_max_reflection_loops,
        },
    )
    return compiled



@lru_cache(maxsize=1)
def get_workflow():
    return build_workflow()
