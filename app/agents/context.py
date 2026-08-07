from __future__ import annotations

import json

from app.core.state import PlatformState
from app.kg.traversal import summarise_context


#: Tool payloads carry the numbers the Reasoning and Validation Agents must
#: ground against. Truncating mid-JSON silently removes evidence and makes
#: correct claims look unsupported, so the budget is generous and the cut is
#: always announced.
_MAX_TOOL_CHARS = 6000
_MAX_TOOL_TOTAL_CHARS = 18000


def render_graph(state: PlatformState) -> str:
    context = state.get("graph_context")
    if context is None or context.is_empty:
        return "(knowledge graph returned no results)"
    return summarise_context(context)


def render_search(state: PlatformState, *, limit: int = 6) -> str:
    results = state.get("search_results") or []
    if not results:
        return "(no search results)"
    lines: list[str] = []
    for index, result in enumerate(results[:limit], start=1):
        lines.append(
            f"[S{index}] ({result.origin}) {result.title} — {result.source}\n"
            f"      {result.snippet[:400]}"
        )
    return "\n".join(lines)


def render_tools(state: PlatformState) -> str:
    results = state.get("tool_results") or []
    if not results:
        return "(no tools executed)"

    lines: list[str] = []
    budget = _MAX_TOOL_TOTAL_CHARS

    for result in results:
        if not result.ok:
            lines.append(f"[T:{result.tool}] FAILED — {result.error}")
            continue

        try:
            payload = json.dumps(result.output, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            payload = str(result.output)

        allowance = min(_MAX_TOOL_CHARS, max(budget, 0))
        if len(payload) > allowance:
            payload = (
                payload[:allowance]
                + f" …[TRUNCATED: {len(payload) - allowance} more characters. "
                "Do not treat missing fields as absent data — say the output "
                "was truncated.]"
            )
        budget -= len(payload)
        lines.append(f"[T:{result.tool}] {payload}")

    return "\n".join(lines)


def render_evidence_bundle(state: PlatformState) -> str:
    """The single context block shared by reasoning, optimisation and validation."""

    return "\n\n".join(
        [
            "=== KNOWLEDGE GRAPH ===",
            render_graph(state),
            "=== SEARCH RESULTS ===",
            render_search(state),
            "=== TOOL OUTPUT ===",
            render_tools(state),
        ]
    )


def evidence_inventory(state: PlatformState) -> dict[str, int]:
    context = state.get("graph_context")
    return {
        "graph_nodes": len(context.nodes) if context else 0,
        "graph_relationships": len(context.relationships) if context else 0,
        "search_results": len(state.get("search_results") or []),
        "tool_results": len(
            [result for result in (state.get("tool_results") or []) if result.ok]
        ),
    }


def has_any_evidence(state: PlatformState) -> bool:
    return any(value > 0 for value in evidence_inventory(state).values())
