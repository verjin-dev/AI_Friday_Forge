from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from app.core.models import (
    AgentTrace,
    ExecutionPlan,
    Explanation,
    GraphContext,
    GuardrailResult,
    OptimizationResult,
    ReasoningOutput,
    ReflectionVerdict,
    RunMetrics,
    SearchResult,
    SecurityVerdict,
    ToolResult,
    ValidationReport,
)



def merge_metrics(left: RunMetrics | None, right: RunMetrics | None) -> RunMetrics:
    """Reducer so parallel branches can each report usage without clobbering."""

    if left is None:
        return right or RunMetrics()
    if right is None:
        return left
    return RunMetrics(
        total_latency_ms=max(left.total_latency_ms, right.total_latency_ms),
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        completion_tokens=left.completion_tokens + right.completion_tokens,
        estimated_cost_usd=left.estimated_cost_usd + right.estimated_cost_usd,
        llm_calls=left.llm_calls + right.llm_calls,
        tool_calls=left.tool_calls + right.tool_calls,
        graph_queries=left.graph_queries + right.graph_queries,
        reflection_loops=max(left.reflection_loops, right.reflection_loops),
    )


def merge_graph_context(
    left: GraphContext | None, right: GraphContext | None
) -> GraphContext:
    """Union of two graph slices, de-duplicated by element id."""

    if left is None:
        return right or GraphContext()
    if right is None:
        return left

    nodes = {node.id: node for node in left.nodes}
    nodes.update({node.id: node for node in right.nodes})
    rels = {rel.id: rel for rel in left.relationships}
    rels.update({rel.id: rel for rel in right.relationships})

    return GraphContext(
        nodes=list(nodes.values()),
        relationships=list(rels.values()),
        cypher=[*left.cypher, *right.cypher],
        records=[*left.records, *right.records],
        hops=max(left.hops, right.hops),
        note=right.note or left.note,
    )


def merge_search_results(
    left: list[SearchResult] | None, right: list[SearchResult] | None
) -> list[SearchResult]:
    """Append search results, de-duplicating across reflection retries."""

    combined = [*(left or []), *(right or [])]
    best: dict[str, SearchResult] = {}
    for result in combined:
        key = (result.url or f"{result.source}|{result.title}").lower()
        existing = best.get(key)
        if existing is None or result.score > existing.score:
            best[key] = result
    return list(best.values())


def take_last(left: Any, right: Any) -> Any:
    """Last writer wins — used for single-owner keys touched by retries."""

    return right if right is not None else left


class PlatformState(TypedDict, total=False):
    """State carried through the LangGraph workflow.

    Keys written by more than one concurrent node carry an explicit reducer;
    everything else is single-owner and uses default overwrite semantics.
    """

    # --- request ---
    trace_id: str
    session_id: str
    role: str
    question: str
    original_question: str
    history: list[dict[str, str]]

    # --- agent outputs ---
    plan: ExecutionPlan | None
    security: SecurityVerdict | None
    guardrail: GuardrailResult | None
    graph_context: Annotated[GraphContext | None, merge_graph_context]
    search_results: Annotated[list[SearchResult], merge_search_results]
    tool_results: Annotated[list[ToolResult], operator.add]
    reasoning: ReasoningOutput | None
    optimization: OptimizationResult | None
    validation: ValidationReport | None
    reflection: ReflectionVerdict | None
    explanation: Explanation | None

    # --- response ---
    answer: str
    blocked: bool
    blocked_reason: str | None

    # --- control / observability ---
    traces: Annotated[list[AgentTrace], operator.add]
    metrics: Annotated[RunMetrics, merge_metrics]
    errors: Annotated[list[str], operator.add]
    reflection_loops: int
    reflection_notes: list[str]
    langsmith_url: str | None


def new_state(
    *,
    trace_id: str,
    session_id: str,
    question: str,
    role: str,
    history: list[dict[str, str]] | None = None,
) -> PlatformState:
    return PlatformState(
        trace_id=trace_id,
        session_id=session_id,
        role=role,
        question=question,
        original_question=question,
        history=history or [],
        plan=None,
        security=None,
        guardrail=None,
        graph_context=None,
        search_results=[],
        tool_results=[],
        reasoning=None,
        optimization=None,
        validation=None,
        reflection=None,
        explanation=None,
        answer="",
        blocked=False,
        blocked_reason=None,
        traces=[],
        metrics=RunMetrics(),
        errors=[],
        reflection_loops=0,
        reflection_notes=[],
        langsmith_url=None,
    )
