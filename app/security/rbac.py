from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import settings
from app.core.models import SecurityFinding, SecuritySeverity


@dataclass(frozen=True, slots=True)
class Role:
    name: str
    description: str
    tools: set[str] = field(default_factory=set)
    #: Node labels this role may see; empty set means "all".
    restricted_labels: set[str] = field(default_factory=set)
    can_see_pii: bool = False


#: Tool names must match the identifiers registered in :mod:`app.mcp.registry`.
_READ_TOOLS = {
    "graph_query",
    "graph_schema",
    "route_plan",
    "network_status",
    "replan_route",
    "route_monitor_start",
    "route_monitor_status",
    "route_monitor_poll",
    "evaluate_constraints",
    "generate_realtime_incidents",
    "algorithm_list",
    "document_search",
    "web_search",
    "weather_lookup",
    "maps_route",
    "sql_query",
    "rest_get",
    "file_read",
    "file_list",
}

ROLES: dict[str, Role] = {
    "admin": Role(
        name="admin",
        description="Platform administrator — full tool and data access.",
        tools=_READ_TOOLS | {"rest_post"},
        can_see_pii=True,
    ),
    "ops_manager": Role(
        name="ops_manager",
        description="Logistics operations manager — full operational visibility.",
        tools=_READ_TOOLS,
        can_see_pii=True,
    ),
    "dispatcher": Role(
        name="dispatcher",
        description="Route and fleet dispatcher — operational, no commercial data.",
        tools=_READ_TOOLS - {"sql_query"},
        restricted_labels={"Policy", "Contract", "Invoice"},
    ),
    "analyst": Role(
        name="analyst",
        description="Business analyst — read-only analytics across the estate.",
        tools=_READ_TOOLS - {"rest_get", "file_read"},
    ),
    "auditor": Role(
        name="auditor",
        description="Compliance auditor — evidence and policy focused.",
        tools={
            "graph_query",
            "graph_schema",
            "network_status",
            "document_search",
            "file_read",
            "file_list",
        },
        can_see_pii=True,
    ),
    "viewer": Role(
        name="viewer",
        description="Read-only viewer — knowledge graph and search only.",
        tools={
            "graph_query",
            "graph_schema",
            "route_plan",
            "network_status",
            "document_search",
        },
        restricted_labels={"Policy", "Contract", "Invoice", "Driver", "User"},
    ),
}


def resolve_role(role: str | None) -> Role:
    key = (role or settings.security_default_role or "viewer").strip().lower()
    return ROLES.get(key, ROLES["viewer"])


def allowed_tools_for(role: str | None) -> list[str]:
    return sorted(resolve_role(role).tools)


def validate_tool_access(
    role: str | None, requested: list[str]
) -> tuple[list[str], list[SecurityFinding]]:
    """Split requested tools into permitted and denied for the caller's role."""

    resolved = resolve_role(role)
    permitted: list[str] = []
    findings: list[SecurityFinding] = []

    for tool in requested:
        if tool in resolved.tools:
            permitted.append(tool)
        else:
            findings.append(
                SecurityFinding(
                    check="access:tool_denied",
                    severity=SecuritySeverity.MEDIUM,
                    detail=(
                        f"Role '{resolved.name}' is not permitted to use tool "
                        f"'{tool}'; the step was dropped from the plan."
                    ),
                )
            )
    return permitted, findings


def label_is_visible(role: str | None, labels: list[str]) -> bool:
    resolved = resolve_role(role)
    if not resolved.restricted_labels:
        return True
    return not set(labels) & resolved.restricted_labels


def filter_visible_labels(
    role: str | None, labels: list[str]
) -> tuple[list[str], list[str]]:
    resolved = resolve_role(role)
    visible = [label for label in labels if label not in resolved.restricted_labels]
    hidden = [label for label in labels if label in resolved.restricted_labels]
    return visible, hidden
