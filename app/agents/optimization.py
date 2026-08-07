from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.base import AgentOutcome, BaseAgent
from app.agents.context import render_evidence_bundle
from app.core.models import AgentName, AgentStatus, OptimizationOption, OptimizationResult
from app.core.state import PlatformState
from app.domain.constraints import (
    ConstraintReport,
    RouteCandidate,
    constraint_catalogue,
    evaluate_candidate,
    get_constraint_profile,
    report_to_text,
)
from app.domain.delay import predict_with_live_traffic
from app.domain.network import RoadNetwork, RoutePath, load_network
from app.mcp.builtin import weather_lookup
from app.llm.structured import LLMUsage, structured_call


class CandidateSet(BaseModel):
    objective: str = Field(
        default="", description="What is being optimised, in one line."
    )
    candidates: list[RouteCandidate] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    note: str | None = None


class RouteRequest(BaseModel):
    """Origin/destination extraction — the only thing the model decides here."""

    is_route_question: bool = Field(
        description="True if the user is asking how to get from one place to another."
    )
    origin: str | None = Field(default=None, description="Start location name only.")
    destination: str | None = Field(default=None, description="End location name only.")


_ROUTE_SYSTEM = """Extract the origin and destination from a logistics question.

Return location names exactly as the user wrote them — do not expand \
abbreviations, add districts or guess places that were not mentioned. If the \
question is not about travelling between two places, set `is_route_question` to \
false and leave both fields null.

Known locations in the network:
{locations}"""


_SYSTEM = """You are the Optimization Agent of an enterprise logistics platform.

You propose candidate solutions — routes, vehicle or driver allocations, \
schedules, reassignments — and populate the structured fields the platform's \
constraint engine needs to verify them.

Critical rules:
1. Populate every field you have real evidence for. LEAVE A FIELD NULL if the \
evidence does not contain it. A null field is reported as unverifiable; a \
fabricated one produces a false feasibility verdict, which is far worse.
2. Never invent capacities, weights, driving hours, timings or temperatures.
3. Propose 2 to 4 genuinely different candidates. Include at least one \
conservative option that is very likely to satisfy every constraint.
4. Do not pre-filter on constraints — propose, and let the engine decide. But do \
not propose something you already know breaches a hard constraint unless it is \
the only option available, and say so in the description.
5. Times must be ISO-8601 (`2026-08-07T14:30`) or `HH:MM`.

The engine will check these hard constraints. A candidate that fails any of \
them is disqualified outright and cannot be recommended:
{constraints}"""


class OptimizationAgent(BaseAgent):
    """Generates candidate solutions and enforces hard logistics constraints.

    Feasibility is decided by :mod:`app.domain.constraints`, not by the model.
    A candidate that breaches a hard constraint is removed from consideration —
    it is never traded off against cost or speed.
    """

    name = AgentName.OPTIMIZATION

    def should_skip(self, state: PlatformState) -> str | None:
        blocked = super().should_skip(state)
        if blocked:
            return blocked
        plan = state.get("plan")
        if plan and AgentName.OPTIMIZATION not in plan.selected_agents:
            return "Not selected by the planner."
        return None

    async def run(self, state: PlatformState) -> AgentOutcome:
        question = state["question"]
        reasoning = state.get("reasoning")
        profile = get_constraint_profile()
        usage = LLMUsage()

        network = await load_network()

        # ------------------------------------------------------------------
        # Preferred path: routes computed from the graph, not proposed by an LLM.
        # ------------------------------------------------------------------
        if network.locations:
            deterministic, route_usage, note = await self._plan_from_network(
                question, network, state
            )
            usage.add(route_usage)
            if deterministic is not None:
                return self._build_outcome(
                    deterministic,
                    profile=profile,
                    objective=note or f"Route options for: {question[:160]}",
                    usage=usage,
                    method="graph_pathfinding",
                    assumptions=[
                        "Routes derived from CONNECTED_TO / ALTERNATE_ROUTE edges "
                        "and incident status in the knowledge graph."
                    ],
                )

        context = [f"Business question: {question}", ""]
        if reasoning and reasoning.summary:
            context += ["Analysis so far:", reasoning.summary, ""]
        context += [render_evidence_bundle(state)]

        candidate_set, candidate_usage = await structured_call(
            CandidateSet,
            system=_SYSTEM.format(constraints=constraint_catalogue()),
            user="\n".join(context),
            fallback=CandidateSet(note="Optimisation model unavailable."),
        )
        usage.add(candidate_usage)

        # Any route the model proposed is checked against the real network
        # before it is allowed near the constraint engine.
        if network.locations:
            for candidate in candidate_set.candidates:
                _verify_against_network(candidate, network)

        if not candidate_set.candidates:
            return AgentOutcome(
                updates={
                    "optimization": OptimizationResult(
                        objective=candidate_set.objective or question[:200],
                        constraints=[constraint_catalogue()],
                        method="constraint_filtered_ranking",
                    )
                },
                summary=candidate_set.note or "No candidate solutions could be formed.",
                status=AgentStatus.SKIPPED,
                detail={"note": candidate_set.note},
                usage=usage,
            )

        return self._build_outcome(
            candidate_set.candidates,
            profile=profile,
            objective=candidate_set.objective or question[:200],
            usage=usage,
            method="constraint_filtered_ranking",
            assumptions=candidate_set.assumptions,
        )

    # ------------------------------------------------------------------
    async def _plan_from_network(
        self, question: str, network: RoadNetwork, state: PlatformState
    ) -> tuple[list[RouteCandidate] | None, LLMUsage, str | None]:
        """Resolve origin/destination, then let the graph produce the routes."""

        request, usage = await structured_call(
            RouteRequest,
            system=_ROUTE_SYSTEM.format(
                locations=", ".join(sorted(network.locations)) or "(none loaded)"
            ),
            user=question,
            fallback=RouteRequest(is_route_question=False),
        )

        if not request.is_route_question or not request.origin or not request.destination:
            return None, usage, None

        origin = network.resolve(request.origin)
        destination = network.resolve(request.destination)
        if not origin or not destination:
            unknown = request.origin if not origin else request.destination
            self.log.info(
                "Route endpoint not in network", extra={"location": unknown}
            )
            return None, usage, None

        paths = network.plan(origin, destination)
        if not paths:
            # No path at all is a real answer, expressed as a single infeasible
            # candidate so the constraint report explains it.
            return (
                [
                    RouteCandidate(
                        label=f"{origin} → {destination}",
                        description=(
                            "No connected road path exists between these locations "
                            "in the network graph."
                        ),
                        stops=[origin, destination],
                        legs_verified=False,
                        missing_legs=[f"{origin} → {destination}"],
                    )
                ],
                usage,
                f"Route from {origin} to {destination}",
            )

        # Reuse the Tool Agent's route_plan output when it covers this journey.
        # Recomputing would call the live traffic API again and produce a
        # second, slightly different ETA for the same route — which reads as a
        # contradiction in the final answer.
        reused = _predictions_from_tool_results(state, origin, destination)

        if reused:
            self.log.info(
                "Reusing route_plan predictions", extra={"routes": len(reused)}
            )
            etas = reused
        else:
            weather: dict | None = None
            try:
                weather = await weather_lookup(destination)
            except Exception as exc:  # noqa: BLE001 - weather is optional context
                self.log.info("Weather unavailable", extra={"error": str(exc)[:160]})

            predictions = await predict_with_live_traffic(paths, weather=weather)
            etas = {
                path.label: {
                    "total": prediction.predicted_total_minutes,
                    "free_flow": prediction.free_flow_minutes,
                    "delay": prediction.predicted_delay_minutes,
                    "risk": prediction.risk.value,
                    "live": prediction.live_traffic_used,
                }
                for path, prediction in zip(paths, predictions)
            }

        candidates: list[RouteCandidate] = []
        for path in paths:
            candidate = _path_to_candidate(path)
            eta = etas.get(path.label)
            if eta:
                # Ranking and the SLA constraints work in time, not just distance.
                candidate.duration_minutes = eta["total"]
                candidate.description = (
                    f"{candidate.description} · ETA {eta['total']:.0f} min "
                    f"({eta['free_flow']:.0f} baseline + {eta['delay']:.0f} delay, "
                    f"{eta['risk']} risk"
                    + (", live traffic" if eta.get("live") else ", no live traffic")
                    + ")"
                )
            candidates.append(candidate)

        return candidates, usage, f"Route from {origin} to {destination}"

    def _build_outcome(
        self,
        candidates: list[RouteCandidate],
        *,
        profile,
        objective: str,
        usage: LLMUsage,
        method: str,
        assumptions: list[str],
    ) -> AgentOutcome:
        reports = [evaluate_candidate(candidate, profile) for candidate in candidates]

        feasible: list[OptimizationOption] = []
        rejected: list[OptimizationOption] = []
        for candidate, report in zip(candidates, reports):
            option = _to_option(candidate, report)
            (feasible if report.feasible else rejected).append(option)

        feasible.sort(key=lambda option: option.score, reverse=True)

        result = OptimizationResult(
            objective=objective,
            recommended=feasible[0] if feasible else None,
            alternatives=feasible[1:],
            rejected=rejected,
            constraints=[constraint_catalogue()],
            constraint_reports=[report.model_dump(mode="json") for report in reports],
            all_infeasible=not feasible,
            method=method,
        )

        if feasible:
            summary = (
                f"{len(feasible)}/{len(reports)} candidate(s) feasible; "
                f"recommending '{result.recommended.label}'"
            )
            if rejected:
                summary += f"; {len(rejected)} disqualified on hard constraints"
        else:
            summary = (
                f"All {len(reports)} candidate(s) breach a hard constraint — "
                "no compliant option exists on the current evidence"
            )

        return AgentOutcome(
            updates={"optimization": result},
            summary=summary,
            detail={
                "method": method,
                "assumptions": assumptions,
                "constraint_report": report_to_text(reports),
                "feasible": [option.label for option in feasible],
                "rejected": {
                    option.label: option.hard_violations for option in rejected
                },
            },
            usage=usage,
        )


def _predictions_from_tool_results(
    state: PlatformState, origin: str, destination: str
) -> dict[str, dict]:
    """Pull ETAs out of an existing ``route_plan`` tool result, if one matches.

    Returns an empty mapping when no usable result is present, in which case
    the caller computes fresh predictions.
    """

    for result in reversed(state.get("tool_results") or []):
        if result.tool != "route_plan" or not result.ok:
            continue
        payload = result.output
        if not isinstance(payload, dict) or not payload.get("found"):
            continue
        if payload.get("origin") != origin or payload.get("destination") != destination:
            continue

        etas: dict[str, dict] = {}
        for route in payload.get("routes") or []:
            label = route.get("label")
            if not label:
                continue
            etas[label] = {
                "total": route.get("predicted_total_minutes", 0.0),
                "free_flow": route.get("free_flow_minutes", 0.0),
                "delay": route.get("predicted_delay_minutes", 0.0),
                "risk": route.get("delay_risk", "low"),
                "live": route.get("live_traffic_used", False),
            }
        if etas:
            return etas

    return {}


def _path_to_candidate(path: RoutePath) -> RouteCandidate:
    """Convert a graph-derived path into a constraint-checkable candidate.

    Every field here originates in the knowledge graph, so the constraint
    engine is verifying data, not a model's assertion.
    """

    return RouteCandidate(
        label=path.label or " → ".join(path.stops),
        description=path.describe(),
        stops=path.stops,
        distance_km=path.total_distance_km,
        network_distance_km=path.total_distance_km,
        stop_count=len(path.stops),
        legs_verified=path.legs_verified,
        blocking_incidents=[item.describe() for item in path.blocking_incidents],
        endpoint_incidents=[item.describe() for item in path.endpoint_incidents],
        advisory_incidents=[item.describe() for item in path.advisory_incidents],
    )


def _verify_against_network(candidate: RouteCandidate, network: RoadNetwork) -> None:
    """Check a model-proposed route against the real network, in place.

    Without this, a plausible-sounding but non-existent road would sail through
    the constraint engine as merely 'unverifiable'.
    """

    if len(candidate.stops) < 2:
        return

    verified, legs, missing = network.verify_legs(candidate.stops)
    candidate.legs_verified = verified
    candidate.missing_legs = missing
    if verified:
        candidate.network_distance_km = round(
            sum(leg.distance_km for leg in legs), 2
        )

    blocking: list[str] = []
    endpoint: list[str] = []
    advisory: list[str] = []
    last = len(candidate.stops) - 1
    for index, stop in enumerate(candidate.stops):
        resolved = network.resolve(stop) or stop
        target = endpoint if index in (0, last) else blocking
        target.extend(item.describe() for item in network.blocking_at(resolved))
        advisory.extend(item.describe() for item in network.advisory_at(resolved))
    candidate.blocking_incidents = blocking
    candidate.endpoint_incidents = endpoint
    candidate.advisory_incidents = advisory


def _to_option(
    candidate: RouteCandidate, report: ConstraintReport
) -> OptimizationOption:
    """Score a candidate: feasibility first, then efficiency, minus soft penalties."""

    score = 1.0 if report.feasible else 0.0

    # Reward verifiability — an option we could actually check is worth more
    # than one that passed only because its data was missing.
    verified = len(report.checks)
    unverified = len(report.unverifiable)
    if verified + unverified:
        score += 0.3 * (verified / (verified + unverified))

    if candidate.duration_minutes:
        score += 0.2 * (1 / (1 + candidate.duration_minutes / 600))
    if candidate.cost:
        score += 0.2 * (1 / (1 + candidate.cost / 1000))
    if candidate.distance_km:
        # Shorter routes rank higher, but never ahead of a feasible longer one.
        score += 0.4 * (1 / (1 + candidate.distance_km / 100))

    score -= report.penalty

    risk = "low"
    if not report.feasible:
        risk = "high"
    elif report.soft_violations or unverified > verified:
        risk = "medium"

    trade_offs = [check.detail for check in report.soft_violations]
    if unverified:
        trade_offs.append(
            f"{unverified} constraint(s) could not be verified from available data."
        )

    return OptimizationOption(
        label=candidate.label,
        description=candidate.description,
        score=round(max(score, 0.0), 3),
        cost=candidate.cost,
        duration_minutes=candidate.duration_minutes,
        distance_km=candidate.distance_km,
        risk=risk,
        trade_offs=trade_offs,
        feasible=report.feasible,
        hard_violations=[check.detail for check in report.hard_violations],
        soft_violations=[check.detail for check in report.soft_violations],
        unverified_constraints=report.unverifiable,
        penalty=round(report.penalty, 3),
    )
