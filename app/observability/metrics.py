from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger


logger = get_logger(__name__)


def load_recent_runs(limit: int = 50) -> list[dict[str, Any]]:
    """Read the tail of the run log, newest first."""

    path = settings.metrics_store_path
    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        logger.error("Could not read run log", extra={"error": str(exc)})
        return []

    runs: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(runs) >= limit:
            break
    return runs


def summarise_runs(limit: int = 200) -> dict[str, Any]:
    """Aggregate platform-level metrics for the observability dashboard."""

    runs = load_recent_runs(limit)
    if not runs:
        return {
            "runs": 0,
            "blocked": 0,
            "avg_latency_ms": 0.0,
            "avg_confidence": None,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "pass_rate": None,
            "agent_latency_ms": {},
            "slowest_agents": [],
        }

    latencies: list[float] = []
    confidences: list[float] = []
    tokens = 0
    cost = 0.0
    blocked = 0
    passed = 0
    scored = 0
    agent_totals: Counter[str] = Counter()
    agent_counts: Counter[str] = Counter()

    for run in runs:
        metrics = run.get("metrics") or {}
        latencies.append(float(metrics.get("total_latency_ms") or 0.0))
        tokens += int(metrics.get("prompt_tokens") or 0) + int(
            metrics.get("completion_tokens") or 0
        )
        cost += float(metrics.get("estimated_cost_usd") or 0.0)
        if run.get("blocked"):
            blocked += 1
        confidence = run.get("confidence")
        if confidence is not None:
            confidences.append(float(confidence))
        if run.get("passed") is not None:
            scored += 1
            passed += 1 if run["passed"] else 0

        for entry in run.get("timeline") or []:
            agent = entry.get("agent")
            if agent:
                agent_totals[agent] += float(entry.get("latency_ms") or 0.0)
                agent_counts[agent] += 1

    agent_latency = {
        agent: round(agent_totals[agent] / agent_counts[agent], 2)
        for agent in agent_totals
        if agent_counts[agent]
    }
    slowest = sorted(agent_latency.items(), key=lambda item: item[1], reverse=True)

    return {
        "runs": len(runs),
        "blocked": blocked,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2),
        "avg_confidence": (
            round(sum(confidences) / len(confidences), 3) if confidences else None
        ),
        "total_tokens": tokens,
        "total_cost_usd": round(cost, 4),
        "pass_rate": round(passed / scored, 3) if scored else None,
        "agent_latency_ms": agent_latency,
        "slowest_agents": [
            {"agent": agent, "avg_latency_ms": value} for agent, value in slowest[:5]
        ],
    }
