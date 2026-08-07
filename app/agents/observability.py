from __future__ import annotations

import json
from datetime import datetime, timezone

from app.agents.base import AgentOutcome, BaseAgent
from app.core.config import settings
from app.core.models import AgentName, RunMetrics
from app.core.state import PlatformState


class ObservabilityAgent(BaseAgent):
    """Aggregates the run: agent timeline, tokens, latency and cost.

    Emits one JSONL record per run so the dashboard and any external collector
    can read execution history without touching the graph.
    """

    name = AgentName.OBSERVABILITY

    def should_skip(self, state: PlatformState) -> str | None:
        return None  # Observability records blocked runs too.

    async def run(self, state: PlatformState) -> AgentOutcome:
        traces = state.get("traces") or []
        metrics: RunMetrics = state.get("metrics") or RunMetrics()
        validation = state.get("validation")

        wall_clock = sum(trace.latency_ms for trace in traces)
        metrics = RunMetrics(
            total_latency_ms=round(wall_clock, 2),
            prompt_tokens=metrics.prompt_tokens,
            completion_tokens=metrics.completion_tokens,
            estimated_cost_usd=round(metrics.estimated_cost_usd, 6),
            llm_calls=metrics.llm_calls,
            tool_calls=metrics.tool_calls,
            graph_queries=metrics.graph_queries,
            reflection_loops=state.get("reflection_loops", 0),
        )

        langsmith_url = None
        if settings.langsmith_tracing and settings.langsmith_api_key:
            langsmith_url = (
                f"https://smith.langchain.com/o/projects/p/"
                f"{settings.langsmith_project}?searchModel="
                f"%7B%22filter%22%3A%22{state.get('trace_id', '')}%22%7D"
            )

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": state.get("trace_id"),
            "session_id": state.get("session_id"),
            "role": state.get("role"),
            "question": (state.get("original_question") or "")[:500],
            "blocked": state.get("blocked", False),
            "confidence": validation.confidence if validation else None,
            "passed": validation.passed if validation else None,
            "metrics": metrics.model_dump(mode="json"),
            "timeline": [
                {
                    "agent": trace.agent.value,
                    "status": trace.status.value,
                    "latency_ms": trace.latency_ms,
                    "tokens": trace.total_tokens,
                    "summary": trace.summary[:200],
                }
                for trace in traces
            ],
            "errors": state.get("errors") or [],
        }

        try:
            settings.metrics_store_path.parent.mkdir(parents=True, exist_ok=True)
            with settings.metrics_store_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except OSError as exc:
            self.log.error("Failed to persist run metrics", extra={"error": str(exc)})

        slowest = max(traces, key=lambda trace: trace.latency_ms, default=None)

        return AgentOutcome(
            updates={"metrics": metrics, "langsmith_url": langsmith_url},
            summary=(
                f"{len(traces)} agent step(s), {metrics.total_tokens} tokens, "
                f"{metrics.total_latency_ms:.0f} ms, "
                f"${metrics.estimated_cost_usd:.4f}"
            ),
            detail={
                "slowest_agent": slowest.agent.value if slowest else None,
                "slowest_ms": slowest.latency_ms if slowest else 0,
                "llm_calls": metrics.llm_calls,
                "tool_calls": metrics.tool_calls,
                "graph_queries": metrics.graph_queries,
                "reflection_loops": metrics.reflection_loops,
                "langsmith_url": langsmith_url,
            },
        )
