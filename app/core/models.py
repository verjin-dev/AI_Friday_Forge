from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentName(str, Enum):
    PLANNER = "planner"
    SECURITY = "security"
    SEARCH = "search"
    KNOWLEDGE = "knowledge"
    TOOL = "tool"
    REASONING = "reasoning"
    OPTIMIZATION = "optimization"
    VALIDATION = "validation"
    REFLECTION = "reflection"
    EXPLANATION = "explanation"
    OBSERVABILITY = "observability"
    SELF_IMPROVING = "self_improving"


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


# ----------------------------------------------------------------------
# Planning
# ----------------------------------------------------------------------
class Intent(BaseModel):
    """What the user is actually asking for, in machine-usable form."""

    summary: str
    category: str = Field(
        default="general",
        description="e.g. route_optimisation, incident_rca, shipment_status, policy_lookup",
    )
    entities: list[str] = Field(default_factory=list)
    time_scope: str | None = None
    requires_live_data: bool = False
    ambiguous: bool = False
    clarifying_question: str | None = None


class PlanStep(BaseModel):
    id: str
    agent: AgentName
    objective: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    parallel_group: int = 0
    rationale: str | None = None


class ExecutionPlan(BaseModel):
    intent: Intent
    steps: list[PlanStep] = Field(default_factory=list)
    selected_agents: list[AgentName] = Field(default_factory=list)
    suggested_tools: list[str] = Field(default_factory=list)
    strategy_note: str | None = None

    def agents_in_group(self, group: int) -> list[AgentName]:
        return [step.agent for step in self.steps if step.parallel_group == group]


# ----------------------------------------------------------------------
# Security
# ----------------------------------------------------------------------
class SecuritySeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SecurityFinding(BaseModel):
    check: str
    severity: SecuritySeverity
    detail: str
    span: str | None = Field(
        default=None, description="Redacted excerpt of the offending text."
    )


class SecurityVerdict(BaseModel):
    allowed: bool = True
    findings: list[SecurityFinding] = Field(default_factory=list)
    redacted_text: str | None = None
    blocked_reason: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    role: str = "analyst"

    @property
    def max_severity(self) -> SecuritySeverity:
        order = list(SecuritySeverity)
        if not self.findings:
            return SecuritySeverity.INFO
        return max(self.findings, key=lambda f: order.index(f.severity)).severity


# ----------------------------------------------------------------------
# Knowledge graph
# ----------------------------------------------------------------------
class GraphNode(BaseModel):
    id: str
    labels: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)

    @property
    def display(self) -> str:
        for key in ("name", "title", "id", "code", "reference"):
            if key in self.properties:
                return str(self.properties[key])
        return self.id


class GraphRelationship(BaseModel):
    id: str
    type: str
    start: str
    end: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphContext(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    relationships: list[GraphRelationship] = Field(default_factory=list)
    cypher: list[str] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)
    hops: int = 0
    note: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.nodes and not self.records


class GraphSchema(BaseModel):
    labels: list[str] = Field(default_factory=list)
    relationship_types: list[str] = Field(default_factory=list)
    node_properties: dict[str, list[str]] = Field(default_factory=dict)
    relationship_properties: dict[str, list[str]] = Field(default_factory=dict)
    patterns: list[str] = Field(
        default_factory=list, description="(:A)-[:REL]->(:B) triples."
    )
    indexes: list[str] = Field(default_factory=list)
    fulltext_indexes: list[str] = Field(default_factory=list)
    vector_indexes: list[str] = Field(default_factory=list)
    node_count: int = 0
    source: Literal["live", "ontology-default", "unavailable"] = "unavailable"
    fetched_at: datetime = Field(default_factory=_now)

    def to_prompt(self) -> str:
        lines = [f"Node labels: {', '.join(self.labels) or '(none)'}"]
        lines.append(
            f"Relationship types: {', '.join(self.relationship_types) or '(none)'}"
        )
        if self.patterns:
            lines.append("Known patterns:")
            lines.extend(f"  {pattern}" for pattern in self.patterns[:60])
        if self.node_properties:
            lines.append("Node properties:")
            for label, props in list(self.node_properties.items())[:40]:
                lines.append(f"  {label}: {', '.join(props[:25])}")
        if self.fulltext_indexes:
            lines.append(f"Full-text indexes: {', '.join(self.fulltext_indexes)}")
        if self.vector_indexes:
            lines.append(f"Vector indexes: {', '.join(self.vector_indexes)}")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Search & tools
# ----------------------------------------------------------------------
class SearchResult(BaseModel):
    title: str
    snippet: str
    source: str
    url: str | None = None
    score: float = 0.0
    origin: Literal["graph", "document", "web", "metadata"] = "graph"


class ToolCall(BaseModel):
    tool: str
    server: str = "builtin"
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class ToolResult(BaseModel):
    tool: str
    server: str = "builtin"
    ok: bool = True
    output: Any = None
    error: str | None = None
    latency_ms: float = 0.0
    started_at: datetime = Field(default_factory=_now)


# ----------------------------------------------------------------------
# Reasoning / optimisation
# ----------------------------------------------------------------------
class Evidence(BaseModel):
    claim: str
    support: str
    origin: Literal["graph", "document", "web", "tool", "model"] = "graph"
    reference: str | None = None


class Finding(BaseModel):
    statement: str
    kind: Literal["root_cause", "impact", "risk", "observation", "dependency"] = (
        "observation"
    )
    confidence: float = 0.5
    evidence: list[Evidence] = Field(default_factory=list)


class Recommendation(BaseModel):
    action: str
    rationale: str
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    owner: str | None = None
    expected_effect: str | None = None


class ReasoningOutput(BaseModel):
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class OptimizationOption(BaseModel):
    label: str
    description: str
    score: float = 0.0
    cost: float | None = None
    duration_minutes: float | None = None
    distance_km: float | None = None
    risk: Literal["low", "medium", "high"] = "medium"
    trade_offs: list[str] = Field(default_factory=list)

    # --- constraint verdict (populated by the constraint engine) ---
    feasible: bool = True
    hard_violations: list[str] = Field(default_factory=list)
    soft_violations: list[str] = Field(default_factory=list)
    unverified_constraints: list[str] = Field(default_factory=list)
    penalty: float = 0.0


class OptimizationResult(BaseModel):
    objective: str = ""
    recommended: OptimizationOption | None = None
    alternatives: list[OptimizationOption] = Field(default_factory=list)
    #: Candidates disqualified by a hard constraint, kept for transparency.
    rejected: list[OptimizationOption] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    constraint_reports: list[dict[str, Any]] = Field(default_factory=list)
    all_infeasible: bool = False
    method: str = "constraint_filtered_ranking"
    engine_report: dict[str, Any] = Field(default_factory=dict)
    algorithm_used: str = ""
    candidates_evaluated: int = 0


# ----------------------------------------------------------------------
# Validation / reflection / explanation
# ----------------------------------------------------------------------
class ValidationIssue(BaseModel):
    kind: Literal[
        "unsupported_claim", "inconsistency", "data_gap", "format", "policy"
    ]
    detail: str
    severity: SecuritySeverity = SecuritySeverity.MEDIUM


class ValidationReport(BaseModel):
    passed: bool = True
    confidence: float = 0.5
    grounded_claims: int = 0
    total_claims: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)
    note: str | None = None


class ReflectionVerdict(BaseModel):
    should_retry: bool = False
    critique: str = ""
    improvements: list[str] = Field(default_factory=list)
    retry_agents: list[AgentName] = Field(default_factory=list)


class SourceReference(BaseModel):
    label: str
    origin: Literal["graph", "document", "web", "tool"] = "graph"
    detail: str | None = None
    url: str | None = None


class Explanation(BaseModel):
    rationale: str = ""
    decision_trace: list[str] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    confidence: float = 0.5


# ----------------------------------------------------------------------
# Observability
# ----------------------------------------------------------------------
class AgentTrace(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    agent: AgentName
    status: AgentStatus = AgentStatus.PENDING
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    latency_ms: float = 0.0
    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class RunMetrics(BaseModel):
    total_latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    graph_queries: int = 0
    reflection_loops: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# ----------------------------------------------------------------------
# API contract
# ----------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    role: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)
    stream: bool = True


class ChatResponse(BaseModel):
    trace_id: str
    session_id: str
    answer: str
    blocked: bool = False
    plan: ExecutionPlan | None = None
    security: SecurityVerdict | None = None
    graph_context: GraphContext | None = None
    search_results: list[SearchResult] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    reasoning: ReasoningOutput | None = None
    optimization: OptimizationResult | None = None
    validation: ValidationReport | None = None
    reflection: ReflectionVerdict | None = None
    explanation: Explanation | None = None
    traces: list[AgentTrace] = Field(default_factory=list)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    langsmith_url: str | None = None
