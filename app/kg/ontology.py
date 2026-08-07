from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Ontology:
    """Domain vocabulary used to frame prompts before the live schema loads.

    Once the customer graph is present, :mod:`app.kg.introspect` replaces this
    with the real labels; the ontology remains the fallback and the source of
    domain-specific question framing.
    """

    domain: str
    node_labels: list[str] = field(default_factory=list)
    relationship_types: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    key_questions: list[str] = field(default_factory=list)
    entity_hints: dict[str, list[str]] = field(default_factory=dict)


#: Generic enterprise backbone shared by every domain (from the platform spec).
_CORE_NODES = [
    "User",
    "Organization",
    "Customer",
    "Policy",
    "Asset",
    "Incident",
    "Document",
]

_CORE_RELATIONSHIPS = [
    "ASSIGNED_TO",
    "RELATED_TO",
    "DEPENDS_ON",
    "AFFECTED_BY",
    "MANAGED_BY",
    "CONNECTED_TO",
    "HAS_POLICY",
    "HAS_INCIDENT",
]


#: The delivered logistics graph: a road network with incidents overlaid.
#: Labels, relationship types and properties match the ingestion schema exactly —
#: nothing here is aspirational, so generated Cypher stays executable.
LOGISTICS_ONTOLOGY = Ontology(
    domain="logistics",
    node_labels=["Location", "Incident"],
    relationship_types=["CONNECTED_TO", "HAS_INCIDENT", "ALTERNATE_ROUTE"],
    patterns=[
        "(:Location)-[:CONNECTED_TO {distance_km, road_name}]->(:Location)",
        "(:Incident)-[:HAS_INCIDENT]->(:Location)",
        "(:Location)-[:ALTERNATE_ROUTE {via, extra_distance}]->(:Location)",
    ],
    key_questions=[
        "What is the best route from A to B right now?",
        "Which routes are blocked by active incidents?",
        "What is the alternate route if the primary road is disrupted?",
        "How much extra distance does the diversion add?",
        "Which locations near Thiruvananthapuram are currently affected?",
        "What is the impact of this incident on connected routes?",
    ],
    entity_hints={
        "location": ["Location"],
        "disruption": ["Incident"],
        "route": ["CONNECTED_TO", "ALTERNATE_ROUTE"],
    },
)

#: Property vocabulary, used to frame prompts and validate generated Cypher.
LOGISTICS_PROPERTIES: dict[str, list[str]] = {
    "Location": ["location_id", "name", "type", "is_near_tvm"],
    "Incident": ["incident_id", "type", "severity", "status"],
    "CONNECTED_TO": ["distance_km", "road_name"],
    "ALTERNATE_ROUTE": ["via", "extra_distance"],
}

#: Enumerated property values the data actually uses.
LOGISTICS_VALUES: dict[str, list[str]] = {
    "Location.type": ["City", "Town"],
    "Location.is_near_tvm": ["Yes", "No"],
    "Incident.severity": ["Critical", "High", "Medium"],
    "Incident.status": ["Active", "Inactive"],
}


_GENERIC_ONTOLOGY = Ontology(
    domain="generic",
    node_labels=_CORE_NODES,
    relationship_types=_CORE_RELATIONSHIPS,
    patterns=[
        "(:User)-[:ASSIGNED_TO]->(:Organization)",
        "(:Asset)-[:MANAGED_BY]->(:Organization)",
        "(:Incident)-[:AFFECTED_BY]->(:Asset)",
    ],
    key_questions=[
        "What caused this issue?",
        "What depends on this entity?",
        "Which policy applies here?",
    ],
)


_REGISTRY: dict[str, Ontology] = {
    "logistics": LOGISTICS_ONTOLOGY,
    "generic": _GENERIC_ONTOLOGY,
}


def value_hints() -> list[str]:
    """Enumerated property values, so generated Cypher filters on real strings."""

    return [
        f"{key} is one of: {', '.join(values)}"
        for key, values in LOGISTICS_VALUES.items()
    ]


def ontology_for_domain(domain: str | None = None) -> Ontology:
    from app.core.config import settings

    key = (domain or settings.platform_domain or "generic").strip().lower()
    return _REGISTRY.get(key, _GENERIC_ONTOLOGY)


def register_ontology(ontology: Ontology) -> None:
    """Extension point for additional enterprise domains."""

    _REGISTRY[ontology.domain.lower()] = ontology
