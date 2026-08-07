from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.core.models import AgentName, AgentStatus, AgentTrace, RunMetrics
from app.core.state import PlatformState
from app.llm.structured import LLMUsage


logger = get_logger(__name__)


@dataclass(slots=True)
class AgentOutcome:
    """What an agent produced, before it is folded back into graph state."""

    updates: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    usage: LLMUsage = field(default_factory=LLMUsage)
    status: AgentStatus = AgentStatus.COMPLETED
    graph_queries: int = 0
    tool_calls: int = 0


class BaseAgent(ABC):
    """Common contract for every agent node in the LangGraph workflow.

    Subclasses implement :meth:`run`; this base handles timing, trace emission,
    token accounting and failure isolation so one agent cannot abort the run.
    """

    name: AgentName

    def __init__(self) -> None:
        self.log = get_logger(f"agent.{self.name.value}")

    @abstractmethod
    async def run(self, state: PlatformState) -> AgentOutcome:
        """Do the agent's work and return state updates."""

    def should_skip(self, state: PlatformState) -> str | None:
        """Return a reason to skip, or ``None`` to execute."""

        if state.get("blocked"):
            return "Request was blocked by the Security Agent."
        return None

    async def __call__(self, state: PlatformState) -> dict[str, Any]:
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc)

        skip_reason = self.should_skip(state)
        if skip_reason:
            return {
                "traces": [
                    AgentTrace(
                        agent=self.name,
                        status=AgentStatus.SKIPPED,
                        started_at=started_at,
                        finished_at=datetime.now(timezone.utc),
                        summary=skip_reason,
                    )
                ]
            }

        try:
            outcome = await self.run(state)
        except Exception as exc:  # noqa: BLE001 - agents degrade, they don't crash
            self.log.exception("Agent failed", extra={"agent": self.name.value})
            latency = round((time.perf_counter() - started) * 1000, 2)
            return {
                "traces": [
                    AgentTrace(
                        agent=self.name,
                        status=AgentStatus.FAILED,
                        started_at=started_at,
                        finished_at=datetime.now(timezone.utc),
                        latency_ms=latency,
                        summary=f"{self.name.value} failed: {exc}",
                        error=str(exc)[:500],
                    )
                ],
                "errors": [f"{self.name.value}: {exc}"],
            }

        latency = round((time.perf_counter() - started) * 1000, 2)
        trace = AgentTrace(
            agent=self.name,
            status=outcome.status,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            latency_ms=latency,
            summary=outcome.summary,
            detail=outcome.detail,
            prompt_tokens=outcome.usage.prompt_tokens,
            completion_tokens=outcome.usage.completion_tokens,
        )

        updates: dict[str, Any] = dict(outcome.updates)
        updates["traces"] = [trace]
        updates["metrics"] = RunMetrics(
            total_latency_ms=latency,
            prompt_tokens=outcome.usage.prompt_tokens,
            completion_tokens=outcome.usage.completion_tokens,
            estimated_cost_usd=outcome.usage.cost_usd,
            llm_calls=outcome.usage.calls,
            tool_calls=outcome.tool_calls,
            graph_queries=outcome.graph_queries,
        )

        self.log.info(
            "Agent completed",
            extra={
                "agent": self.name.value,
                "latency_ms": latency,
                "tokens": outcome.usage.prompt_tokens + outcome.usage.completion_tokens,
            },
        )
        return updates
