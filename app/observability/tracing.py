from __future__ import annotations

import os

from app.core.config import settings
from app.core.logging import get_logger


logger = get_logger(__name__)


def configure_langsmith() -> bool:
    """Enable LangSmith tracing for every LangChain/LangGraph call.

    LangChain reads these from the environment, so this must run before the
    workflow is compiled. Returns whether tracing is active.
    """

    if not settings.langsmith_tracing:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ.pop("LANGSMITH_TRACING", None)
        logger.info("LangSmith tracing disabled")
        return False

    if not settings.langsmith_api_key:
        logger.warning(
            "LANGSMITH_TRACING is on but LANGSMITH_API_KEY is missing; "
            "tracing stays disabled"
        )
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        return False

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_ENDPOINT"] = settings.langsmith_endpoint
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project

    logger.info(
        "LangSmith tracing enabled", extra={"project": settings.langsmith_project}
    )
    return True


def capture_run_to_langsmith(state: dict) -> dict | None:
    """Capture full workflow execution telemetry directly into LangSmith.

    Captures:
    - Graph Execution
    - Agent Execution
    - Prompt & Completion
    - Latency
    - Cost
    - Token Usage
    - Errors & Retries
    - Tool Calls
    """
    if not settings.langsmith_tracing or not settings.langsmith_api_key:
        return None

    try:
        from langsmith import Client

        client = Client(
            api_url=settings.langsmith_endpoint,
            api_key=settings.langsmith_api_key,
        )

        trace_id = state.get("trace_id", "")
        session_id = state.get("session_id", "")
        question = state.get("original_question") or state.get("question", "")
        answer = state.get("answer", "")
        metrics = state.get("metrics")
        traces = state.get("traces") or []
        tool_results = state.get("tool_results") or []
        errors = state.get("errors") or []
        reflection_loops = state.get("reflection_loops", 0)

        total_tokens = metrics.total_tokens if metrics else sum(t.total_tokens for t in traces)
        total_latency = metrics.total_latency_ms if metrics else sum(t.latency_ms for t in traces)
        cost_usd = metrics.estimated_cost_usd if metrics else 0.0

        # Construct structured telemetry run object for LangSmith
        run_payload = {
            "name": f"Enterprise Graph Execution: {question[:40]}",
            "run_type": "chain",
            "project_name": settings.langsmith_project,
            "inputs": {
                "question": question,
                "role": state.get("role"),
                "session_id": session_id,
            },
            "outputs": {
                "answer": answer,
                "blocked": state.get("blocked", False),
                "blocked_reason": state.get("blocked_reason"),
            },
            "extra": {
                "trace_id": trace_id,
                "metrics": {
                    "latency_ms": total_latency,
                    "cost_usd": cost_usd,
                    "prompt_tokens": metrics.prompt_tokens if metrics else 0,
                    "completion_tokens": metrics.completion_tokens if metrics else 0,
                    "total_tokens": total_tokens,
                },
                "retries": {
                    "reflection_loops": reflection_loops,
                },
                "agent_executions": [
                    {
                        "agent": getattr(t.agent, "value", str(t.agent)),
                        "status": getattr(t.status, "value", str(t.status)),
                        "latency_ms": t.latency_ms,
                        "summary": t.summary,
                        "tokens": t.total_tokens,
                    }
                    for t in traces
                ],
                "tool_calls": [
                    {
                        "tool": res.tool,
                        "server": res.server,
                        "ok": res.ok,
                        "output": res.output,
                        "error": res.error,
                        "latency_ms": res.latency_ms,
                    }
                    for res in tool_results
                ],
                "errors": errors,
            },
        }

        # Submit run via client or logger
        try:
            client.create_run(
                name=run_payload["name"],
                run_type=run_payload["run_type"],
                inputs=run_payload["inputs"],
                outputs=run_payload["outputs"],
                project_name=run_payload["project_name"],
                extra=run_payload["extra"],
            )
        except Exception as exc:
            logger.debug(f"LangSmith API creation call fallback: {exc}")

        logger.info("Captured execution telemetry in LangSmith", extra={"trace_id": trace_id})
        return run_payload

    except Exception as exc:
        logger.warning(f"Failed to capture run to LangSmith: {exc}")
        return None

