from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.models import GraphContext, GraphSchema
from app.kg.client import collect_graph_elements, get_kg_client


logger = get_logger(__name__)


#: Property names worth matching a free-text term against, in priority order.
_LOOKUP_HINTS = (
    "name",
    "title",
    "id",
    "code",
    "reference",
    "ref",
    "number",
    "label",
    "description",
    "status",
)

#: Relationship semantics used for dependency / impact reasoning.
_DEPENDENCY_TYPES = (
    "DEPENDS_ON",
    "ROUTED_VIA",
    "SHIPPED_FROM",
    "CARRIED_BY",
    "DRIVEN_BY",
    "STORED_AT",
    "SERVICED_BY",
    "CONNECTED_TO",
    "NEXT_STOP",
)

_IMPACT_TYPES = (
    "AFFECTED_BY",
    "DELAYED_BY",
    "HAS_INCIDENT",
    "DELIVERED_TO",
    "ASSIGNED_TO",
    "RELATED_TO",
)


def _lookup_properties(schema: GraphSchema) -> list[str]:
    """Pick the searchable properties actually present in the live schema."""

    candidates: list[str] = []
    for props in schema.node_properties.values():
        for prop in props:
            lowered = prop.lower()
            if any(hint in lowered for hint in _LOOKUP_HINTS) and prop not in candidates:
                candidates.append(prop)
    if not candidates:
        candidates = ["name", "title", "id", "code", "reference", "status"]
    return candidates[:12]


async def discover_entities(
    terms: list[str], schema: GraphSchema, *, limit: int = 25
) -> GraphContext:
    """Entity discovery — locate the nodes a question is actually about.

    Uses a full-text index when the graph has one, otherwise falls back to a
    case-insensitive property scan over the schema's identifying properties.
    """

    terms = [term.strip() for term in terms if term and term.strip()]
    if not terms:
        return GraphContext(note="No entities to look up.")

    client = get_kg_client()

    if schema.fulltext_indexes:
        index = schema.fulltext_indexes[0]
        query = " OR ".join(f'"{term}"' for term in terms[:8])
        cypher = (
            "CALL db.index.fulltext.queryNodes($index, $query) "
            "YIELD node, score RETURN node, score ORDER BY score DESC LIMIT $limit"
        )
        records = await client.try_run(
            cypher, {"index": index, "query": query, "limit": limit}
        )
        if records:
            nodes, rels = collect_graph_elements(records)
            return GraphContext(
                nodes=nodes,
                relationships=rels,
                cypher=[cypher],
                records=records,
                note=f"Matched via full-text index '{index}'.",
            )

    props = _lookup_properties(schema)
    predicate = " OR ".join(
        f"(n.`{prop}` IS NOT NULL AND toLower(toString(n.`{prop}`)) CONTAINS term)"
        for prop in props
    )
    cypher = (
        "UNWIND $terms AS raw WITH toLower(raw) AS term "
        f"MATCH (n) WHERE {predicate} "
        "RETURN DISTINCT n LIMIT $limit"
    )
    records = await client.try_run(cypher, {"terms": terms, "limit": limit})
    nodes, rels = collect_graph_elements(records)
    return GraphContext(
        nodes=nodes,
        relationships=rels,
        cypher=[cypher],
        records=records,
        note=None if nodes else "No matching entities found in the graph.",
    )


async def expand_neighbourhood(
    node_ids: list[str], *, hops: int = 2, limit: int | None = None
) -> GraphContext:
    """Multi-hop reasoning — pull the subgraph surrounding the seed nodes."""

    if not node_ids:
        return GraphContext()

    hops = max(1, min(hops, 3))
    cap = limit or settings.neo4j_max_rows
    cypher = (
        "MATCH (n) WHERE elementId(n) IN $ids "
        f"MATCH path = (n)-[*1..{hops}]-(m) "
        "RETURN path LIMIT $limit"
    )
    records = await get_kg_client().try_run(
        cypher, {"ids": node_ids[:25], "limit": cap}
    )
    nodes, rels = collect_graph_elements(records)
    return GraphContext(
        nodes=nodes,
        relationships=rels,
        cypher=[cypher],
        records=[],
        hops=hops,
        note=f"Expanded {len(node_ids)} seed node(s) to {hops} hop(s).",
    )


async def dependency_analysis(
    node_ids: list[str], schema: GraphSchema, *, hops: int = 3
) -> GraphContext:
    """What this entity relies on — upstream chain."""

    return await _typed_walk(
        node_ids,
        schema,
        _DEPENDENCY_TYPES,
        direction="out",
        hops=hops,
        note="Upstream dependency chain.",
    )


async def impact_analysis(
    node_ids: list[str], schema: GraphSchema, *, hops: int = 3
) -> GraphContext:
    """What is affected if this entity fails — downstream blast radius."""

    return await _typed_walk(
        node_ids,
        schema,
        _IMPACT_TYPES,
        direction="in",
        hops=hops,
        note="Downstream impact / blast radius.",
    )


async def _typed_walk(
    node_ids: list[str],
    schema: GraphSchema,
    preferred: tuple[str, ...],
    *,
    direction: str,
    hops: int,
    note: str,
) -> GraphContext:
    if not node_ids:
        return GraphContext()

    available = [rel for rel in preferred if rel in set(schema.relationship_types)]
    rel_filter = f":{'|'.join(available)}" if available else ""
    hops = max(1, min(hops, 3))

    if direction == "out":
        pattern = f"(n)-[{rel_filter}*1..{hops}]->(m)"
    else:
        pattern = f"(m)-[{rel_filter}*1..{hops}]->(n)"

    cypher = (
        "MATCH (n) WHERE elementId(n) IN $ids "
        f"MATCH path = {pattern} "
        "RETURN path LIMIT $limit"
    )
    records = await get_kg_client().try_run(
        cypher, {"ids": node_ids[:25], "limit": settings.neo4j_max_rows}
    )
    nodes, rels = collect_graph_elements(records)
    return GraphContext(
        nodes=nodes,
        relationships=rels,
        cypher=[cypher],
        hops=hops,
        note=note if nodes else f"{note} No connected entities found.",
    )


async def historical_context(
    node_ids: list[str], *, limit: int = 40
) -> GraphContext:
    """Historical context — prior incidents attached to the seed entities."""

    if not node_ids:
        return GraphContext()

    cypher = (
        "MATCH (n) WHERE elementId(n) IN $ids "
        "MATCH (n)-[r]-(h) "
        "WHERE any(l IN labels(h) WHERE l IN "
        "['Incident','Event','Delay','Exception','Claim','Ticket']) "
        "RETURN n, r, h LIMIT $limit"
    )
    records = await get_kg_client().try_run(
        cypher, {"ids": node_ids[:25], "limit": limit}
    )
    nodes, rels = collect_graph_elements(records)
    return GraphContext(
        nodes=nodes,
        relationships=rels,
        cypher=[cypher],
        note="Historical incidents linked to the entities in scope."
        if nodes
        else "No historical incidents recorded for these entities.",
    )


def summarise_context(context: GraphContext, *, max_items: int = 25) -> str:
    """Render a subgraph as compact text for the reasoning prompt."""

    if context.is_empty:
        return "(no knowledge graph results)"

    by_id = {node.id: node for node in context.nodes}
    lines: list[str] = []

    if context.nodes:
        lines.append("Entities:")
        for node in context.nodes[:max_items]:
            labels = "/".join(node.labels) or "Node"
            props = ", ".join(
                f"{key}={value}"
                for key, value in list(node.properties.items())[:6]
                if value is not None
            )
            lines.append(f"  - ({labels}) {node.display}" + (f" [{props}]" if props else ""))
        if len(context.nodes) > max_items:
            lines.append(f"  ... and {len(context.nodes) - max_items} more entities")

    if context.relationships:
        lines.append("Relationships:")
        for rel in context.relationships[:max_items]:
            start = by_id.get(rel.start)
            end = by_id.get(rel.end)
            lines.append(
                f"  - {start.display if start else rel.start} "
                f"-[{rel.type}]-> {end.display if end else rel.end}"
            )
        if len(context.relationships) > max_items:
            lines.append(
                f"  ... and {len(context.relationships) - max_items} more relationships"
            )

    if context.records:
        lines.append("Query rows (sample):")
        for record in context.records[:8]:
            scalars: dict[str, Any] = {
                key: value
                for key, value in record.items()
                if not isinstance(value, (dict, list))
            }
            if scalars:
                lines.append(f"  - {scalars}")

    return "\n".join(lines)
