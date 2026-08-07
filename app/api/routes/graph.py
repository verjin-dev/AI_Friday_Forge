from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.models import GraphContext, GraphSchema
from app.kg.client import GraphUnavailableError, collect_graph_elements, get_kg_client
from app.kg.cypher import UnsafeCypherError, sanitize
from app.kg.introspect import get_graph_schema, invalidate_schema_cache
from app.kg.traversal import discover_entities, expand_neighbourhood


router = APIRouter(prefix="/api/graph", tags=["knowledge-graph"])


class CypherRequest(BaseModel):
    cypher: str
    parameters: dict = Field(default_factory=dict)


@router.get("/schema", response_model=GraphSchema)
async def schema(refresh: bool = Query(default=False)) -> GraphSchema:
    if refresh:
        invalidate_schema_cache()
    return await get_graph_schema(refresh=refresh)


@router.post("/query")
async def query(request: CypherRequest) -> dict:
    """Read-only Cypher endpoint. Write clauses are rejected before execution."""

    try:
        safe = sanitize(request.cypher)
    except UnsafeCypherError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        records = await get_kg_client().run(safe, request.parameters)
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    nodes, relationships = collect_graph_elements(records)
    return {
        "cypher": safe,
        "row_count": len(records),
        "rows": records,
        "graph": GraphContext(
            nodes=nodes, relationships=relationships, cypher=[safe]
        ).model_dump(mode="json"),
    }


@router.get("/overview", response_model=GraphContext)
async def overview(limit: int = Query(default=75, le=300)) -> GraphContext:
    """A representative slice of the graph, for the visualisation panel."""

    cypher = "MATCH (n)-[r]->(m) RETURN n, r, m LIMIT $limit"
    try:
        records = await get_kg_client().run(cypher, {"limit": limit}, limit=limit)
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    nodes, relationships = collect_graph_elements(records)
    if not nodes:
        # A graph with no relationships still has nodes worth showing.
        records = await get_kg_client().try_run(
            "MATCH (n) RETURN n LIMIT $limit", {"limit": limit}
        )
        nodes, relationships = collect_graph_elements(records)

    return GraphContext(
        nodes=nodes,
        relationships=relationships,
        cypher=[cypher],
        note=None if nodes else "The knowledge graph is empty.",
    )


@router.get("/search", response_model=GraphContext)
async def search(
    terms: str = Query(description="Comma-separated entity terms."),
    hops: int = Query(default=1, ge=0, le=3),
) -> GraphContext:
    """Entity discovery with optional neighbourhood expansion."""

    schema = await get_graph_schema()
    values = [term.strip() for term in terms.split(",") if term.strip()]
    if not values:
        raise HTTPException(status_code=400, detail="at least one term is required")

    context = await discover_entities(values, schema, limit=settings.neo4j_max_rows)
    if hops and context.nodes:
        expansion = await expand_neighbourhood(
            [node.id for node in context.nodes], hops=hops
        )
        merged_nodes = {node.id: node for node in context.nodes}
        merged_nodes.update({node.id: node for node in expansion.nodes})
        merged_rels = {rel.id: rel for rel in context.relationships}
        merged_rels.update({rel.id: rel for rel in expansion.relationships})
        context = GraphContext(
            nodes=list(merged_nodes.values()),
            relationships=list(merged_rels.values()),
            cypher=[*context.cypher, *expansion.cypher],
            hops=hops,
            note=context.note,
        )
    return context
