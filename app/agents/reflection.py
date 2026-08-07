from __future__ import annotations

from app.agents.base import AgentOutcome, BaseAgent
from app.agents.context import evidence_inventory
from app.core.config import settings
from app.core.models import AgentName, AgentStatus, ReflectionVerdict, SecuritySeverity
from app.core.state import PlatformState
from app.llm.structured import LLMUsage, structured_call


_SYSTEM = """You are the Reflection Agent of an enterprise logistics platform.

A response has just failed validation. Decide whether another attempt would \
plausibly succeed, and if so, exactly what should be done differently.

Retry only when the failure is fixable by re-running agents — for example:
- the graph query missed the entity, so `knowledge` should search differently;
- the analysis over-claimed, so `reasoning` should be re-run with stricter grounding;
- a candidate breached a constraint, so `optimization` should propose compliant options.

Do NOT retry when:
- the required data simply is not in the knowledge base (retrying cannot invent it);
- the question is ambiguous and needs the user to clarify;
- validation failed because no compliant option exists — that is a valid answer, \
not an error.

Each improvement must be a concrete instruction to a specific agent, not generic \
advice like "be more accurate"."""


class ReflectionAgent(BaseAgent):
    """Self review, error detection and retry strategy."""

    name = AgentName.REFLECTION

    async def run(self, state: PlatformState) -> AgentOutcome:
        validation = state.get("validation")
        loops = state.get("reflection_loops", 0)
        usage = LLMUsage()

        if validation is None or validation.passed:
            verdict = ReflectionVerdict(
                should_retry=False,
                critique="Validation passed; no rework required.",
            )
            return AgentOutcome(
                updates={"reflection": verdict},
                summary="No rework required.",
                status=AgentStatus.COMPLETED,
            )

        if loops >= settings.workflow_max_reflection_loops:
            verdict = ReflectionVerdict(
                should_retry=False,
                critique=(
                    f"Retry budget exhausted after {loops} attempt(s); responding "
                    "with the issues disclosed to the user."
                ),
            )
            return AgentOutcome(
                updates={"reflection": verdict},
                summary=verdict.critique,
                detail={"loops": loops},
            )

        # A hard-constraint failure with no feasible option is a legitimate
        # outcome — retrying cannot manufacture a compliant solution.
        optimization = state.get("optimization")
        if optimization is not None and optimization.all_infeasible:
            verdict = ReflectionVerdict(
                should_retry=False,
                critique=(
                    "No candidate satisfies the hard logistics constraints. This "
                    "is a valid finding and must be reported as such."
                ),
            )
            return AgentOutcome(
                updates={"reflection": verdict},
                summary=verdict.critique,
                detail={"all_infeasible": True},
            )

        inventory = evidence_inventory(state)
        if not any(inventory.values()):
            verdict = ReflectionVerdict(
                should_retry=False,
                critique=(
                    "No evidence exists in the knowledge base for this question; "
                    "another attempt would not change that."
                ),
                improvements=[
                    "Report the data gap and state what would need to be ingested."
                ],
            )
            return AgentOutcome(
                updates={"reflection": verdict},
                summary=verdict.critique,
                detail=inventory,
            )

        context = [
            f"Question: {state['question']}",
            f"Attempt number: {loops + 1} of {settings.workflow_max_reflection_loops + 1}",
            "",
            f"Validation confidence: {validation.confidence:.2f} "
            f"(threshold {settings.workflow_confidence_threshold:.2f})",
            f"Grounded claims: {validation.grounded_claims}/{validation.total_claims}",
            "",
            "Validation issues:",
            *(
                f"- [{issue.severity.value}] {issue.kind}: {issue.detail}"
                for issue in validation.issues
            ),
            "",
            f"Evidence gathered: {inventory}",
        ]

        verdict, verdict_usage = await structured_call(
            ReflectionVerdict,
            system=_SYSTEM,
            user="\n".join(context),
            fallback=ReflectionVerdict(
                should_retry=False,
                critique="Reflection model unavailable; responding with disclosed issues.",
            ),
        )
        usage.add(verdict_usage)

        critical = any(
            issue.severity is SecuritySeverity.CRITICAL for issue in validation.issues
        )
        if critical and not verdict.should_retry:
            # A critical issue must not be shipped unexamined while budget remains.
            verdict.should_retry = True
            verdict.improvements.append(
                "A critical validation issue was raised; regenerate the "
                "recommendation so it complies with the hard constraints."
            )

        retry_agents = verdict.retry_agents or [AgentName.REASONING]
        verdict.retry_agents = [
            agent
            for agent in retry_agents
            if agent
            in {
                AgentName.KNOWLEDGE,
                AgentName.SEARCH,
                AgentName.TOOL,
                AgentName.REASONING,
                AgentName.OPTIMIZATION,
            }
        ] or [AgentName.REASONING]

        updates: dict = {"reflection": verdict}
        if verdict.should_retry:
            updates["reflection_loops"] = loops + 1
            updates["reflection_notes"] = [
                verdict.critique,
                *verdict.improvements,
            ]

        summary = (
            f"Retry {loops + 1} requested: "
            f"{', '.join(agent.value for agent in verdict.retry_agents)}"
            if verdict.should_retry
            else f"No retry: {verdict.critique[:160]}"
        )

        return AgentOutcome(
            updates=updates,
            summary=summary,
            detail={
                "should_retry": verdict.should_retry,
                "improvements": verdict.improvements,
                "loops": loops,
            },
            usage=usage,
        )
