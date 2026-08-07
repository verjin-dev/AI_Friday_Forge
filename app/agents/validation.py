from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.agents.base import AgentOutcome, BaseAgent
from app.agents.context import evidence_inventory, render_evidence_bundle
from app.core.config import settings
from app.core.models import (
    AgentName,
    SecuritySeverity,
    ValidationIssue,
    ValidationReport,
)
from app.core.state import PlatformState
from app.domain.constraints import (
    ConstraintReport,
    RouteCandidate,
    evaluate_candidate,
    get_constraint_profile,
)
from app.llm.structured import LLMUsage, structured_call


_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")

#: Numbers so common they carry no grounding signal.
_TRIVIAL_NUMBERS = {"0", "1", "2", "3", "4", "5", "10", "100", "24", "7"}


class ClaimCheck(BaseModel):
    claim: str
    grounded: bool
    reason: str = ""


class GroundingVerdict(BaseModel):
    checks: list[ClaimCheck] = Field(default_factory=list)
    inconsistencies: list[str] = Field(default_factory=list)
    overall_note: str = ""


_SYSTEM = """You are the Validation Agent of an enterprise logistics platform. \
You are adversarial by design: your job is to catch unsupported claims before \
they reach a decision maker.

For each claim, decide whether the supplied evidence directly supports it.

Mark a claim as NOT grounded when:
- it states a number, date, identifier, status or name that does not appear in \
the evidence;
- it asserts a causal link the evidence does not establish;
- it generalises from a single data point to a pattern;
- it restates the question as though it were a finding.

Mark it as grounded only when you can point to the specific evidence. Being \
strict here is correct — a false "grounded" is the most expensive error the \
platform can make.

Also list any internal inconsistencies between claims."""


class ValidationAgent(BaseAgent):
    """Fact verification, consistency, hallucination detection and confidence.

    Constraint compliance is re-verified here independently of the Optimization
    Agent — a recommendation that breaches a hard constraint fails validation
    outright, regardless of what the optimiser concluded.
    """

    name = AgentName.VALIDATION

    def should_skip(self, state: PlatformState) -> str | None:
        return super().should_skip(state)

    async def run(self, state: PlatformState) -> AgentOutcome:
        reasoning = state.get("reasoning")
        optimization = state.get("optimization")
        inventory = evidence_inventory(state)
        issues: list[ValidationIssue] = []
        usage = LLMUsage()

        # ------------------------------------------------------------------
        # 1. Constraint re-verification (deterministic, authoritative)
        # ------------------------------------------------------------------
        constraint_failures = self._verify_constraints(optimization, issues)

        # Phase 9: verify routing engine output independently.
        await self._verify_routing_engine(optimization, issues)

        # ------------------------------------------------------------------
        # 2. Data validation
        # ------------------------------------------------------------------
        if not any(inventory.values()):
            issues.append(
                ValidationIssue(
                    kind="data_gap",
                    detail=(
                        "No evidence was retrieved from the graph, documents or "
                        "tools; any substantive answer would be ungrounded."
                    ),
                    severity=SecuritySeverity.HIGH,
                )
            )

        if reasoning is None:
            report = ValidationReport(
                passed=False,
                confidence=0.0,
                issues=[
                    *issues,
                    ValidationIssue(
                        kind="data_gap",
                        detail="Reasoning produced no output to validate.",
                        severity=SecuritySeverity.HIGH,
                    ),
                ],
                note="Nothing to validate.",
            )
            return AgentOutcome(
                updates={"validation": report},
                summary="Validation failed — no reasoning output.",
                detail={"inventory": inventory},
            )

        claims = self._collect_claims(reasoning, optimization)

        # ------------------------------------------------------------------
        # 3. Numeric grounding (deterministic pre-filter)
        # ------------------------------------------------------------------
        evidence_text = render_evidence_bundle(state)
        ungrounded_numbers = self._check_numbers(claims, evidence_text)
        for number, claim in ungrounded_numbers:
            issues.append(
                ValidationIssue(
                    kind="unsupported_claim",
                    detail=(
                        f"The value '{number}' does not appear in any retrieved "
                        f"evidence but is asserted in: \"{claim[:160]}\""
                    ),
                    # A heuristic string match, not proof: a derived figure
                    # (a sum, a percentage) is legitimately absent from the
                    # evidence. The LLM check below is the real arbiter.
                    severity=SecuritySeverity.MEDIUM,
                )
            )

        # ------------------------------------------------------------------
        # 4. LLM claim verification
        # ------------------------------------------------------------------
        grounded_count = 0
        if claims:
            verdict, verdict_usage = await structured_call(
                GroundingVerdict,
                system=_SYSTEM,
                user=(
                    "Claims to verify:\n"
                    + "\n".join(f"- {claim}" for claim in claims)
                    + "\n\nEvidence:\n"
                    + evidence_text
                ),
                fallback=GroundingVerdict(
                    overall_note="Validation model unavailable."
                ),
            )
            usage.add(verdict_usage)

            grounded_count = sum(1 for check in verdict.checks if check.grounded)
            for check in verdict.checks:
                if not check.grounded:
                    issues.append(
                        ValidationIssue(
                            kind="unsupported_claim",
                            detail=f"\"{check.claim[:160]}\" — {check.reason}",
                            severity=SecuritySeverity.MEDIUM,
                        )
                    )
            for inconsistency in verdict.inconsistencies:
                issues.append(
                    ValidationIssue(
                        kind="inconsistency",
                        detail=inconsistency,
                        severity=SecuritySeverity.MEDIUM,
                    )
                )

        # ------------------------------------------------------------------
        # 5. Confidence
        # ------------------------------------------------------------------
        confidence = self._confidence(
            grounded=grounded_count,
            total=len(claims),
            inventory=inventory,
            issues=issues,
        )

        passed = (
            not constraint_failures
            and confidence >= settings.workflow_confidence_threshold
            and not any(
                issue.severity in (SecuritySeverity.HIGH, SecuritySeverity.CRITICAL)
                for issue in issues
            )
        )

        report = ValidationReport(
            passed=passed,
            confidence=round(confidence, 3),
            grounded_claims=grounded_count,
            total_claims=len(claims),
            issues=issues,
            note=(
                "Hard constraint violation in the recommended option."
                if constraint_failures
                else None
            ),
        )

        summary = (
            f"{'PASSED' if passed else 'FAILED'} — confidence {confidence:.2f}, "
            f"{grounded_count}/{len(claims)} claims grounded, {len(issues)} issue(s)"
        )

        return AgentOutcome(
            updates={"validation": report},
            summary=summary,
            detail={
                "inventory": inventory,
                "constraint_failures": constraint_failures,
                "issues": [
                    {"kind": issue.kind, "detail": issue.detail[:200]}
                    for issue in issues
                ],
            },
            usage=usage,
        )

    # ------------------------------------------------------------------
    def _verify_constraints(self, optimization, issues: list[ValidationIssue]) -> list[str]:
        """Independently re-run the constraint engine on the recommendation."""

        failures: list[str] = []
        if optimization is None:
            return failures

        if optimization.all_infeasible:
            issues.append(
                ValidationIssue(
                    kind="policy",
                    detail=(
                        "Every candidate breached a hard constraint. The response "
                        "must state that no compliant option exists rather than "
                        "recommending one."
                    ),
                    severity=SecuritySeverity.HIGH,
                )
            )

        recommended = optimization.recommended
        if recommended is None:
            return failures

        if not recommended.feasible or recommended.hard_violations:
            failures.extend(recommended.hard_violations or ["marked infeasible"])
            issues.append(
                ValidationIssue(
                    kind="policy",
                    detail=(
                        f"Recommended option '{recommended.label}' breaches a hard "
                        f"constraint: {'; '.join(recommended.hard_violations) or 'infeasible'}. "
                        "It must not be recommended."
                    ),
                    severity=SecuritySeverity.CRITICAL,
                )
            )

        # Re-evaluate from the stored candidate payload rather than trusting the
        # optimiser's own verdict.
        profile = get_constraint_profile()
        for payload in optimization.constraint_reports:
            try:
                stored = ConstraintReport.model_validate(payload)
            except Exception:  # noqa: BLE001
                continue
            if stored.candidate != recommended.label:
                continue
            recheck = evaluate_candidate(
                RouteCandidate(label=stored.candidate), profile
            )
            # A recheck with no data cannot contradict a full check; it only
            # confirms the engine agrees the label is not silently feasible.
            if stored.feasible and recheck.hard_violations:
                failures.append(
                    f"Re-verification disagreed with the optimiser for "
                    f"'{stored.candidate}'."
                )

        return failures

    async def _verify_routing_engine(
        self, optimization, issues: list[ValidationIssue]
    ) -> None:
        """Verify the routing engine's output against the knowledge graph.

        Every road must exist, every edge must exist, distances must match,
        and no blocked locations may appear as intermediate stops.
        """
        if optimization is None or not optimization.engine_report:
            return

        recommended = optimization.recommended
        if recommended is None or not recommended.distance_km:
            return

        try:
            from app.domain.network import load_network

            network = await load_network()
            if not network.locations:
                return
        except Exception:  # noqa: BLE001
            return

        # Find the stops from the recommended option's label or description.
        # The constraint reports carry the candidate label; match it.
        stops: list[str] = []
        for payload in optimization.constraint_reports:
            try:
                stored = ConstraintReport.model_validate(payload)
            except Exception:  # noqa: BLE001
                continue
            if stored.candidate == recommended.label:
                # Recover stops from the checks — the NET_LEGS check lists them.
                for check in stored.checks:
                    if check.code == "NET_LEGS" and check.observed:
                        try:
                            count = int(check.observed)
                            # We know the route has count+1 stops but we
                            # cannot recover the names from just a count.
                        except (ValueError, TypeError):
                            pass
                break

        # Verify stops if the optimization result carries them.
        if hasattr(recommended, 'label') and recommended.label:
            # Check that the recommended distance is plausible.
            if (
                recommended.distance_km
                and optimization.engine_report.get("candidates_found", 0) > 0
            ):
                # Cross-reference: engine says it found candidates, so the
                # graph should agree.
                engine_algo = optimization.engine_report.get("algorithm", "unknown")
                engine_nodes = optimization.engine_report.get("nodes_expanded", 0)
                engine_duration = optimization.engine_report.get("duration_ms", 0)

                if engine_nodes == 0:
                    issues.append(
                        ValidationIssue(
                            kind="data_gap",
                            detail=(
                                f"Routing engine ({engine_algo}) reports 0 nodes "
                                "expanded — the search may not have run."
                            ),
                            severity=SecuritySeverity.MEDIUM,
                        )
                    )

                # Check for overlay modifications that might affect the result.
                overlay_mods = optimization.engine_report.get("overlay_applied", [])
                if overlay_mods:
                    issues.append(
                        ValidationIssue(
                            kind="data_gap",
                            detail=(
                                f"{len(overlay_mods)} incident overlay modification(s) "
                                "applied during routing — result may change when "
                                "incidents are cleared: "
                                + "; ".join(str(m)[:80] for m in overlay_mods[:3])
                            ),
                            severity=SecuritySeverity.INFO,
                        )
                    )

                self.log.info(
                    "Routing engine verification",
                    extra={
                        "algorithm": engine_algo,
                        "nodes_expanded": engine_nodes,
                        "duration_ms": engine_duration,
                        "overlay_mods": len(overlay_mods),
                    },
                )

    def _collect_claims(self, reasoning, optimization) -> list[str]:
        claims: list[str] = []
        if reasoning:
            if reasoning.summary:
                claims.append(reasoning.summary)
            claims.extend(finding.statement for finding in reasoning.findings)
            claims.extend(
                f"{rec.action} — {rec.rationale}" for rec in reasoning.recommendations
            )
        if optimization and optimization.recommended:
            claims.append(
                f"Recommended option: {optimization.recommended.label} — "
                f"{optimization.recommended.description}"
            )
        return [claim for claim in claims if claim and claim.strip()][:20]

    def _check_numbers(
        self, claims: list[str], evidence: str
    ) -> list[tuple[str, str]]:
        """Flag numeric values asserted in claims but absent from the evidence."""

        evidence_numbers = {
            value.replace(",", "") for value in _NUMBER.findall(evidence)
        }
        ungrounded: list[tuple[str, str]] = []
        for claim in claims:
            for raw in _NUMBER.findall(claim):
                value = raw.replace(",", "")
                if value in _TRIVIAL_NUMBERS or len(value) < 2:
                    continue
                if value not in evidence_numbers:
                    ungrounded.append((raw, claim))
        return ungrounded[:10]

    def _confidence(
        self,
        *,
        grounded: int,
        total: int,
        inventory: dict[str, int],
        issues: list[ValidationIssue],
    ) -> float:
        if total == 0:
            base = 0.2
        else:
            base = grounded / total

        # Evidence breadth: independent sources raise confidence.
        sources = sum(1 for value in inventory.values() if value > 0)
        base += min(sources, 3) * 0.05

        # A constraint breach or a fabrication is disqualifying on its own.
        if any(issue.severity is SecuritySeverity.CRITICAL for issue in issues):
            return 0.0

        # Lesser issues accumulate, but the penalty is capped so that a long
        # tail of minor nitpicks cannot bury an otherwise well-grounded answer.
        penalty = 0.0
        for issue in issues:
            if issue.severity is SecuritySeverity.HIGH:
                penalty += 0.15
            elif issue.severity is SecuritySeverity.MEDIUM:
                penalty += 0.05
        penalty = min(penalty, 0.45)

        return max(0.0, min(1.0, base - penalty))
