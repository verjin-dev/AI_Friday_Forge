from __future__ import annotations

from app.agents.base import AgentOutcome, BaseAgent
from app.agents.context import render_evidence_bundle
from app.core.models import AgentName, Explanation, SourceReference
from app.core.state import PlatformState
from app.domain.constraints import ConstraintReport, report_to_text
from app.kg.ontology import ontology_for_domain
from app.llm.structured import LLMUsage, text_call
from app.security.guardrails import apply_output_guardrails


_SYSTEM = """You are the Explanation Agent of an enterprise {domain} platform. \
You write the final response the business user reads.

Write for an operations professional: direct, specific, decision-ready. Use \
markdown with short sections. No preamble, no restating the question.

Absolute rules:
1. State only what the validated analysis supports. Never add facts, numbers or \
identifiers of your own.
2. If a hard constraint disqualified an option, say which constraint and why — \
in plain operational language, not rule codes alone.
3. If NO option satisfies the constraints, say so plainly and explain what would \
have to change (more capacity, a later promise date, a second vehicle). Do not \
soften it into a recommendation.
4. Disclose material gaps and low confidence honestly, near the top, not buried.
5. Attribute key facts to their source inline — the entity name, document or tool.
6. If validation raised unresolved issues, include a short "Caveats" section.
7. When routing engine data is available, reference the specific algorithm used \
(Dijkstra, A*, or Yen's K-Shortest Path), the number of candidates evaluated, \
the overlay modifications applied, and the engine's confidence score. Never \
generate a generic explanation — every statement must cite specific evidence.

Structure, adapted to the question:
- **Answer** — the direct response in 1-3 sentences.
- **Why** — the reasoning and root cause, with evidence.
- **Recommended action** — including constraint status, if the question calls for one.
- **Caveats** — gaps, assumptions, unverified constraints, low confidence."""


class ExplanationAgent(BaseAgent):
    """Response generation with source attribution and decision trace."""

    name = AgentName.EXPLANATION

    def should_skip(self, state: PlatformState) -> str | None:
        # A blocked request already carries its own user-facing answer.
        if state.get("blocked"):
            return "Request was blocked; security message returned instead."
        return None

    async def run(self, state: PlatformState) -> AgentOutcome:
        question = state.get("original_question") or state["question"]
        reasoning = state.get("reasoning")
        optimization = state.get("optimization")
        validation = state.get("validation")
        reflection = state.get("reflection")
        ontology = ontology_for_domain()
        usage = LLMUsage()

        context = [f"Business question: {question}", ""]

        if reasoning:
            context += ["=== VALIDATED ANALYSIS ===", f"Summary: {reasoning.summary}"]
            if reasoning.findings:
                context.append("Findings:")
                for finding in reasoning.findings:
                    references = ", ".join(
                        item.reference or item.origin for item in finding.evidence
                    )
                    context.append(
                        f"  - [{finding.kind}] {finding.statement} "
                        f"(confidence {finding.confidence:.2f}"
                        + (f"; source: {references}" if references else "")
                        + ")"
                    )
            if reasoning.recommendations:
                context.append("Recommendations:")
                for rec in reasoning.recommendations:
                    context.append(
                        f"  - [{rec.priority}] {rec.action} — {rec.rationale}"
                    )
            if reasoning.unknowns:
                context.append("Known gaps: " + "; ".join(reasoning.unknowns))

        if optimization:
            context += ["", "=== CONSTRAINT-CHECKED OPTIONS ==="]
            context.append(f"Objective: {optimization.objective}")
            if optimization.all_infeasible:
                context.append(
                    "NO CANDIDATE IS COMPLIANT — every option breached a hard "
                    "constraint. Report this outcome; do not recommend any option."
                )
            elif optimization.recommended:
                option = optimization.recommended
                context.append(
                    f"Recommended: {option.label} — {option.description} "
                    f"(score {option.score}, risk {option.risk})"
                )
                if option.soft_violations:
                    context.append(
                        "  Soft violations: " + "; ".join(option.soft_violations)
                    )
                if option.unverified_constraints:
                    context.append(
                        "  Unverified constraints (missing data): "
                        + ", ".join(option.unverified_constraints)
                    )
            for option in optimization.alternatives:
                context.append(f"Alternative: {option.label} — {option.description}")
            for option in optimization.rejected:
                context.append(
                    f"DISQUALIFIED: {option.label} — "
                    + "; ".join(option.hard_violations)
                )

        # === Routing engine telemetry ===
        if optimization and optimization.engine_report:
            er = optimization.engine_report
            context += [
                "",
                "=== ROUTING ENGINE ===",
                f"Algorithm: {er.get('algorithm', 'unknown')} "
                f"(reason: {er.get('algorithm_reason', 'auto-selected')})",
                f"Graph: {er.get('graph_nodes', '?')} nodes",
                f"Candidates: {er.get('candidates_found', 0)} found "
                f"of {er.get('candidates_requested', 0)} requested "
                f"in {er.get('duration_ms', 0):.0f} ms",
                f"Nodes expanded: {er.get('nodes_expanded', 0)}",
            ]
            overlay = er.get("overlay_applied", [])
            if overlay:
                context.append(
                    f"Incident overlay: {len(overlay)} modification(s) — "
                    + "; ".join(str(m)[:100] for m in overlay[:4])
                )
            notes = er.get("notes", [])
            if notes:
                context.append("Engine notes: " + "; ".join(notes[:3]))

        if validation:
            context += [
                "",
                "=== VALIDATION ===",
                f"Passed: {validation.passed}; confidence {validation.confidence:.2f}; "
                f"{validation.grounded_claims}/{validation.total_claims} claims grounded",
            ]
            for issue in validation.issues[:8]:
                context.append(f"  - [{issue.severity.value}] {issue.kind}: {issue.detail}")

        if reflection and reflection.critique:
            context += ["", f"Reflection: {reflection.critique}"]

        context += ["", "=== RAW EVIDENCE (for attribution only) ===",
                    render_evidence_bundle(state)[:6000]]

        try:
            answer, answer_usage = await text_call(
                system=_SYSTEM.format(domain=ontology.domain),
                user="\n".join(context),
            )
            usage.add(answer_usage)
        except Exception as exc:  # noqa: BLE001
            self.log.error("Answer generation failed", extra={"error": str(exc)[:300]})
            answer = _fallback_answer(state)

        safe_answer, guardrail_findings = apply_output_guardrails(
            answer, role=state.get("role")
        )

        sources = _collect_sources(state)
        explanation = Explanation(
            rationale=(reasoning.summary if reasoning else "")[:2000],
            decision_trace=_decision_trace(state),
            sources=sources,
            confidence=validation.confidence if validation else 0.3,
        )

        return AgentOutcome(
            updates={"answer": safe_answer, "explanation": explanation},
            summary=(
                f"Response generated — {len(sources)} source(s), "
                f"confidence {explanation.confidence:.2f}"
                + (
                    f", {len(guardrail_findings)} guardrail finding(s)"
                    if guardrail_findings
                    else ""
                )
            ),
            detail={
                "guardrails": [
                    {"check": finding.check, "severity": finding.severity.value}
                    for finding in guardrail_findings
                ],
                "source_count": len(sources),
                "constraint_summary": _constraint_summary(optimization),
            },
            usage=usage,
        )


def _constraint_summary(optimization) -> str | None:
    """Readable constraint verdicts for the trace panel."""

    if optimization is None or not optimization.constraint_reports:
        return None
    reports = []
    for payload in optimization.constraint_reports:
        try:
            reports.append(ConstraintReport.model_validate(payload))
        except Exception:  # noqa: BLE001 - a malformed report must not break the answer
            continue
    return report_to_text(reports) if reports else None


def _fallback_answer(state: PlatformState) -> str:
    reasoning = state.get("reasoning")
    if reasoning and reasoning.summary:
        return (
            f"{reasoning.summary}\n\n"
            "_(Response generation was degraded; this is the raw analysis summary.)_"
        )
    return (
        "The platform could not produce a grounded answer for this request. "
        "No supporting data was retrieved from the knowledge graph, documents "
        "or connected tools."
    )


def _collect_sources(state: PlatformState) -> list[SourceReference]:
    sources: list[SourceReference] = []

    context = state.get("graph_context")
    if context:
        for node in context.nodes[:12]:
            sources.append(
                SourceReference(
                    label=node.display,
                    origin="graph",
                    detail="/".join(node.labels) or "Node",
                )
            )

    for result in (state.get("search_results") or [])[:8]:
        sources.append(
            SourceReference(
                label=result.title,
                origin="web" if result.origin == "web" else "document",
                detail=result.source,
                url=result.url,
            )
        )

    for result in state.get("tool_results") or []:
        if result.ok:
            sources.append(
                SourceReference(
                    label=result.tool,
                    origin="tool",
                    detail=f"{result.server} · {result.latency_ms:.0f} ms",
                )
            )

    return sources


def _decision_trace(state: PlatformState) -> list[str]:
    trace: list[str] = []
    for entry in state.get("traces") or []:
        trace.append(
            f"{entry.agent.value}: {entry.status.value} "
            f"({entry.latency_ms:.0f} ms) — {entry.summary}"
        )
    optimization = state.get("optimization")
    if optimization and optimization.engine_report:
        er = optimization.engine_report
        trace.append(
            f"Routing engine: {er.get('algorithm', '?')} selected "
            f"({er.get('algorithm_reason', 'auto')}), "
            f"{er.get('candidates_found', 0)}/{er.get('candidates_requested', 0)} "
            f"candidates in {er.get('duration_ms', 0):.0f} ms"
        )
        overlay_count = len(er.get('overlay_applied', []))
        if overlay_count:
            trace.append(f"Incident overlay: {overlay_count} modification(s) active")
    return trace
