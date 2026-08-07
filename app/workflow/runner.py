from __future__ import annotations

import uuid
from typing import Any, AsyncIterator

from app.core.config import settings
from app.core.logging import get_logger, set_trace_id
from app.core.models import AgentTrace, ChatResponse, RunMetrics
from app.core.state import PlatformState, new_state
from app.workflow.graph import get_workflow


logger = get_logger(__name__)

#: Guard against a pathological reflection loop consuming the run.
_RECURSION_LIMIT = 60


def _config(state: PlatformState) -> dict[str, Any]:
    return {
        "recursion_limit": _RECURSION_LIMIT,
        "configurable": {"thread_id": state["session_id"]},
        "metadata": {
            "trace_id": state["trace_id"],
            "session_id": state["session_id"],
            "role": state["role"],
            "domain": settings.platform_domain,
        },
        "run_name": "enterprise-workflow",
        "tags": ["enterprise-ai-platform", settings.platform_domain],
    }


def prepare_state(
    question: str,
    *,
    session_id: str | None = None,
    role: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> PlatformState:
    trace_id = uuid.uuid4().hex
    set_trace_id(trace_id)
    return new_state(
        trace_id=trace_id,
        session_id=session_id or uuid.uuid4().hex,
        question=question,
        role=role or settings.security_default_role,
        history=history,
    )


def to_response(state: PlatformState) -> ChatResponse:
    return ChatResponse(
        trace_id=state.get("trace_id", ""),
        session_id=state.get("session_id", ""),
        answer=state.get("answer", ""),
        blocked=bool(state.get("blocked")),
        plan=state.get("plan"),
        security=state.get("security"),
        guardrail=state.get("guardrail"),
        graph_context=state.get("graph_context"),
        search_results=state.get("search_results") or [],
        tool_results=state.get("tool_results") or [],
        reasoning=state.get("reasoning"),
        optimization=state.get("optimization"),
        validation=state.get("validation"),
        reflection=state.get("reflection"),
        explanation=state.get("explanation"),
        traces=state.get("traces") or [],
        metrics=state.get("metrics") or RunMetrics(),
        langsmith_url=state.get("langsmith_url"),
    )



async def run_workflow(
    question: str,
    *,
    session_id: str | None = None,
    role: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> ChatResponse:
    """Execute the full workflow and return the assembled response."""

    state = prepare_state(
        question, session_id=session_id, role=role, history=history
    )
    workflow = get_workflow()

    logger.info(
        "Workflow started",
        extra={"question": question[:200], "role": state["role"]},
    )
    final = await workflow.ainvoke(state, config=_config(state))
    logger.info(
        "Workflow finished",
        extra={
            "blocked": final.get("blocked"),
            "agents": len(final.get("traces") or []),
        },
    )
    return to_response(final)


async def stream_workflow(
    question: str,
    *,
    session_id: str | None = None,
    role: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield agent-timeline events as the workflow progresses.

    Event shapes consumed by the UI:
      ``{"event": "start",    "trace_id", "session_id"}``
      ``{"event": "agent",    "agent", "status", "summary", "latency_ms", ...}``
      ``{"event": "complete", "response": {...}}``
      ``{"event": "error",    "message"}``
    """

    state = prepare_state(
        question, session_id=session_id, role=role, history=history
    )
    workflow = get_workflow()

    yield {
        "event": "start",
        "trace_id": state["trace_id"],
        "session_id": state["session_id"],
        "role": state["role"],
    }

    accumulated: dict[str, Any] = dict(state)

    try:
        async for chunk in workflow.astream(
            state, config=_config(state), stream_mode="updates"
        ):
            for node, update in chunk.items():
                if not isinstance(update, dict):
                    continue

                _merge(accumulated, update)

                for trace in update.get("traces") or []:
                    yield {"event": "agent", **_trace_event(trace, node)}

    except Exception as exc:  # noqa: BLE001 - surface failures to the client
        logger.exception("Workflow stream failed")
        yield {"event": "error", "message": str(exc)[:500]}
        return

    yield {
        "event": "complete",
        "response": to_response(accumulated).model_dump(mode="json"),
    }


def _trace_event(trace: AgentTrace, node: str) -> dict[str, Any]:
    return {
        "agent": trace.agent.value,
        "node": node,
        "status": trace.status.value,
        "summary": trace.summary,
        "latency_ms": trace.latency_ms,
        "tokens": trace.total_tokens,
        "detail": trace.detail,
        "error": trace.error,
    }


def _merge(target: dict[str, Any], update: dict[str, Any]) -> None:
    """Mirror LangGraph's reducers so the streamed response matches ainvoke."""

    from app.core.state import (
        merge_graph_context,
        merge_metrics,
        merge_search_results,
    )

    for key, value in update.items():
        if key == "traces":
            target["traces"] = [*(target.get("traces") or []), *(value or [])]
        elif key == "errors":
            target["errors"] = [*(target.get("errors") or []), *(value or [])]
        elif key == "tool_results":
            target["tool_results"] = [
                *(target.get("tool_results") or []),
                *(value or []),
            ]
        elif key == "search_results":
            target["search_results"] = merge_search_results(
                target.get("search_results"), value
            )
        elif key == "graph_context":
            target["graph_context"] = merge_graph_context(
                target.get("graph_context"), value
            )
        elif key == "metrics":
            target["metrics"] = merge_metrics(target.get("metrics"), value)
        else:
            target[key] = value
