from __future__ import annotations

import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.models import GraphSchema
from app.kg.client import GraphUnavailableError, get_kg_client
from app.kg.ontology import LOGISTICS_PROPERTIES, ontology_for_domain


logger = get_logger(__name__)

_cache: tuple[float, GraphSchema] | None = None


def _ontology_schema() -> GraphSchema:
    ontology = ontology_for_domain()
    node_properties = {
        label: list(props)
        for label, props in LOGISTICS_PROPERTIES.items()
        if label in ontology.node_labels
    }
    relationship_properties = {
        rel: list(props)
        for rel, props in LOGISTICS_PROPERTIES.items()
        if rel in ontology.relationship_types
    }
    return GraphSchema(
        labels=list(ontology.node_labels),
        relationship_types=list(ontology.relationship_types),
        node_properties=node_properties,
        relationship_properties=relationship_properties,
        patterns=list(ontology.patterns),
        source="ontology-default",
    )




async def _fetch_labels(client: Any) -> list[str]:
    rows = await client.try_run("CALL db.labels() YIELD label RETURN label ORDER BY label")
    return [row["label"] for row in rows]


async def _fetch_rel_types(client: Any) -> list[str]:
    rows = await client.try_run(
        "CALL db.relationshipTypes() YIELD relationshipType "
        "RETURN relationshipType ORDER BY relationshipType"
    )
    return [row["relationshipType"] for row in rows]


async def _fetch_node_properties(client: Any) -> dict[str, list[str]]:
    rows = await client.try_run(
        "CALL db.schema.nodeTypeProperties() "
        "YIELD nodeLabels, propertyName "
        "RETURN nodeLabels, propertyName"
    )
    properties: dict[str, list[str]] = {}
    for row in rows:
        prop = row.get("propertyName")
        if not prop:
            continue
        for label in row.get("nodeLabels") or []:
            properties.setdefault(label, [])
            if prop not in properties[label]:
                properties[label].append(prop)
    return properties


async def _fetch_rel_properties(client: Any) -> dict[str, list[str]]:
    rows = await client.try_run(
        "CALL db.schema.relTypeProperties() "
        "YIELD relType, propertyName "
        "RETURN relType, propertyName"
    )
    properties: dict[str, list[str]] = {}
    for row in rows:
        rel = (row.get("relType") or "").strip(":`")
        prop = row.get("propertyName")
        if not rel or not prop:
            continue
        properties.setdefault(rel, [])
        if prop not in properties[rel]:
            properties[rel].append(prop)
    return properties


async def _fetch_patterns(client: Any) -> list[str]:
    """Prefer the server-maintained schema graph; fall back to sampling."""

    rows = await client.try_run("CALL db.schema.visualization()")
    patterns: list[str] = []
    if rows:
        record = rows[0]
        nodes_by_id: dict[str, list[str]] = {}
        for node in record.get("nodes") or []:
            if isinstance(node, dict):
                nodes_by_id[node.get("id", "")] = node.get("labels", [])
        for rel in record.get("relationships") or []:
            if not isinstance(rel, dict):
                continue
            start = ":".join(nodes_by_id.get(rel.get("start", ""), []) or ["?"])
            end = ":".join(nodes_by_id.get(rel.get("end", ""), []) or ["?"])
            patterns.append(f"(:{start})-[:{rel.get('rel_type', '?')}]->(:{end})")
    if patterns:
        return sorted(set(patterns))

    sampled = await client.try_run(
        "MATCH (a)-[r]->(b) WITH labels(a) AS a, type(r) AS t, labels(b) AS b "
        "RETURN DISTINCT a, t, b LIMIT 150"
    )
    for row in sampled:
        start = ":".join(row.get("a") or ["?"])
        end = ":".join(row.get("b") or ["?"])
        patterns.append(f"(:{start})-[:{row.get('t')}]->(:{end})")
    return sorted(set(patterns))


async def _fetch_indexes(client: Any) -> tuple[list[str], list[str], list[str]]:
    rows = await client.try_run(
        "SHOW INDEXES YIELD name, type, labelsOrTypes, properties "
        "RETURN name, type, labelsOrTypes, properties"
    )
    all_indexes: list[str] = []
    fulltext: list[str] = []
    vector: list[str] = []
    for row in rows:
        name = row.get("name", "")
        kind = (row.get("type") or "").upper()
        targets = ",".join(row.get("labelsOrTypes") or [])
        props = ",".join(row.get("properties") or [])
        all_indexes.append(f"{name} [{kind}] {targets}({props})")
        if kind == "FULLTEXT":
            fulltext.append(name)
        elif kind == "VECTOR":
            vector.append(name)
    return all_indexes, fulltext, vector


async def get_graph_schema(*, refresh: bool = False) -> GraphSchema:
    """Introspect the live graph, cached for ``neo4j_schema_cache_seconds``.

    Falls back to the domain ontology so planning and Cypher generation still
    work before any data has been loaded.
    """

    global _cache

    now = time.monotonic()
    if not refresh and _cache is not None:
        cached_at, cached = _cache
        if now - cached_at < settings.neo4j_schema_cache_seconds:
            return cached

    client = get_kg_client()
    try:
        await (await client.driver()).verify_connectivity()
    except (GraphUnavailableError, Exception) as exc:  # noqa: BLE001
        logger.warning(
            "Graph schema unavailable, using ontology defaults",
            extra={"error": str(exc)[:200]},
        )
        schema = _ontology_schema()
        _cache = (now, schema)
        return schema

    labels = await _fetch_labels(client)
    rel_types = await _fetch_rel_types(client)
    node_props = await _fetch_node_properties(client)
    rel_props = await _fetch_rel_properties(client)
    patterns = await _fetch_patterns(client)
    indexes, fulltext, vector = await _fetch_indexes(client)

    count_rows = await client.try_run("MATCH (n) RETURN count(n) AS c")
    node_count = int(count_rows[0]["c"]) if count_rows else 0

    if not labels:
        logger.info("Graph reachable but empty; blending ontology defaults")
        fallback = _ontology_schema()
        schema = GraphSchema(
            labels=fallback.labels,
            relationship_types=fallback.relationship_types,
            patterns=fallback.patterns,
            indexes=indexes,
            fulltext_indexes=fulltext,
            vector_indexes=vector,
            node_count=0,
            source="ontology-default",
        )
    else:
        schema = GraphSchema(
            labels=labels,
            relationship_types=rel_types,
            node_properties=node_props,
            relationship_properties=rel_props,
            patterns=patterns,
            indexes=indexes,
            fulltext_indexes=fulltext,
            vector_indexes=vector,
            node_count=node_count,
            source="live",
        )

    _cache = (now, schema)
    logger.info(
        "Graph schema loaded",
        extra={
            "source": schema.source,
            "labels": len(schema.labels),
            "rel_types": len(schema.relationship_types),
            "nodes": schema.node_count,
        },
    )
    return schema


def invalidate_schema_cache() -> None:
    global _cache
    _cache = None
