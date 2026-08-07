from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.agents.base import AgentOutcome, BaseAgent
from app.core.config import settings
from app.core.models import AgentName, AgentStatus
from app.core.state import PlatformState


_TOKEN_MIN_LENGTH = 3


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in "".join(
            char if char.isalnum() else " " for char in text.lower()
        ).split()
        if len(token) >= _TOKEN_MIN_LENGTH
    }


def recall_similar(question: str, *, limit: int = 3) -> list[dict[str, Any]]:
    """Retrieve previously successful workflows for a similar question.

    Used to warm-start planning: if a question of this shape was answered well
    before, the same agent and tool selection is a good prior.
    """

    path = settings.learning_store_path
    if not path.exists():
        return []

    target = _tokens(question)
    if not target:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stored = _tokens(record.get("question", ""))
                if not stored:
                    continue
                overlap = len(target & stored) / len(target | stored)
                if overlap > 0.25:
                    scored.append((overlap, record))
    except OSError:
        return []

    scored.sort(key=lambda item: item[0], reverse=True)
    return [record for _, record in scored[:limit]]


class SelfImprovingAgent(BaseAgent):
    """Learns from executions: stores successful workflows for reuse.

    Only high-confidence, validated runs are persisted — learning from a bad run
    would entrench the mistake.
    """

    name = AgentName.SELF_IMPROVING

    def should_skip(self, state: PlatformState) -> str | None:
        if not settings.workflow_enable_self_improvement:
            return "Self-improvement is disabled by configuration."
        if state.get("blocked"):
            return "Blocked requests are not used as learning examples."
        return None

    async def run(self, state: PlatformState) -> AgentOutcome:
        validation = state.get("validation")
        plan = state.get("plan")

        if validation is None or not validation.passed:
            return AgentOutcome(
                summary="Run not stored — validation did not pass.",
                status=AgentStatus.SKIPPED,
                detail={
                    "passed": validation.passed if validation else None,
                    "confidence": validation.confidence if validation else None,
                },
            )

        if validation.confidence < settings.workflow_confidence_threshold:
            return AgentOutcome(
                summary=(
                    f"Run not stored — confidence {validation.confidence:.2f} "
                    f"below threshold {settings.workflow_confidence_threshold:.2f}."
                ),
                status=AgentStatus.SKIPPED,
            )

        traces = state.get("traces") or []
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": state.get("trace_id"),
            "question": (state.get("original_question") or state.get("question") or "")[:500],
            "intent_category": plan.intent.category if plan else None,
            "agents": [agent.value for agent in plan.selected_agents] if plan else [],
            "tools": (
                [result.tool for result in (state.get("tool_results") or []) if result.ok]
            ),
            "cypher": (
                (state.get("graph_context").cypher[:4])
                if state.get("graph_context")
                else []
            ),
            "confidence": validation.confidence,
            "grounded_ratio": (
                validation.grounded_claims / validation.total_claims
                if validation.total_claims
                else None
            ),
            "reflection_loops": state.get("reflection_loops", 0),
            "latency_ms": round(sum(trace.latency_ms for trace in traces), 2),
        }

        try:
            settings.learning_store_path.parent.mkdir(parents=True, exist_ok=True)
            with settings.learning_store_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except OSError as exc:
            self.log.error("Failed to persist learning record", extra={"error": str(exc)})
            return AgentOutcome(
                summary="Could not persist the successful workflow.",
                status=AgentStatus.FAILED,
            )

        return AgentOutcome(
            summary=(
                f"Stored successful workflow (confidence {validation.confidence:.2f}, "
                f"{len(record['agents'])} agents, {record['reflection_loops']} retry loop(s))"
            ),
            detail=record,
        )
