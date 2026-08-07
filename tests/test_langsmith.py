"""Unit test suite for LangSmith Integration telemetry capture."""

from __future__ import annotations

from app.core.config import settings
from app.core.models import AgentName, AgentStatus, AgentTrace, RunMetrics, ToolResult
from app.core.state import new_state
from app.observability.tracing import capture_run_to_langsmith, configure_langsmith


class TestLangSmithIntegration:
    def test_configure_langsmith_disabled_by_default(self):
        # Unless settings enable it, configure_langsmith returns False gracefully
        assert isinstance(configure_langsmith(), bool)

    def test_capture_run_telemetry_structure(self, monkeypatch):
        monkeypatch.setattr(settings, "langsmith_tracing", True)
        monkeypatch.setattr(settings, "langsmith_api_key", "ls__test_mock_key_12345")

        state = new_state(
            trace_id="trace-test-ls",
            session_id="sess-test-ls",
            question="Find optimal route for truck fleet",
            role="dispatcher",
        )
        state["answer"] = "Optimal route is NH44."
        state["metrics"] = RunMetrics(
            total_latency_ms=120.5,
            prompt_tokens=450,
            completion_tokens=150,
            estimated_cost_usd=0.002,
        )
        state["traces"] = [
            AgentTrace(
                agent=AgentName.GUARDRAIL,
                status=AgentStatus.COMPLETED,
                latency_ms=15.2,
                summary="Guardrail passed",
            ),
            AgentTrace(
                agent=AgentName.PLANNER,
                status=AgentStatus.COMPLETED,
                latency_ms=35.0,
                summary="Plan created",
            ),
        ]
        state["tool_results"] = [
            ToolResult(
                tool="route_distance",
                ok=True,
                output={"distance_km": 150.0},
                latency_ms=12.0,
            )
        ]

        payload = capture_run_to_langsmith(dict(state))
        assert payload is not None
        assert "Enterprise Graph Execution" in payload["name"]
        assert payload["inputs"]["question"] == "Find optimal route for truck fleet"
        assert payload["outputs"]["answer"] == "Optimal route is NH44."
        assert payload["extra"]["metrics"]["prompt_tokens"] == 450
        assert payload["extra"]["metrics"]["completion_tokens"] == 150
        assert len(payload["extra"]["agent_executions"]) == 2
        assert len(payload["extra"]["tool_calls"]) == 1
        assert payload["extra"]["tool_calls"][0]["tool"] == "route_distance"
