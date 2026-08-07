from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from neo4j.graph import Node, Path, Relationship

from app.core.config import settings
from app.core.logging import get_logger
from app.core.models import GraphNode, GraphRelationship


logger = get_logger(__name__)


class GraphUnavailableError(RuntimeError):
    """Raised when Neo4j cannot be reached or is not configured."""


def to_python(value: Any) -> Any:
    """Convert Neo4j driver types into JSON-serialisable Python values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (list, tuple, set)):
        return [to_python(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_python(item) for key, item in value.items()}
    if isinstance(value, Node):
        return {
            "_type": "node",
            "id": value.element_id,
            "labels": list(value.labels),
            "properties": {k: to_python(v) for k, v in dict(value).items()},
        }
    if isinstance(value, Relationship):
        return {
            "_type": "relationship",
            "id": value.element_id,
            "rel_type": value.type,
            "start": value.start_node.element_id if value.start_node else None,
            "end": value.end_node.element_id if value.end_node else None,
            "properties": {k: to_python(v) for k, v in dict(value).items()},
        }
    if isinstance(value, Path):
        return {
            "_type": "path",
            "nodes": [to_python(node) for node in value.nodes],
            "relationships": [to_python(rel) for rel in value.relationships],
        }
    # neo4j spatial / temporal types expose iso or native conversion helpers.
    for attr in ("iso_format", "to_native"):
        converter = getattr(value, attr, None)
        if callable(converter):
            try:
                return to_python(converter())
            except Exception:  # noqa: BLE001 - best effort coercion
                break
    return str(value)


def collect_graph_elements(
    records: list[dict[str, Any]],
) -> tuple[list[GraphNode], list[GraphRelationship]]:
    """Walk arbitrary query results and pull out every node / relationship."""

    nodes: dict[str, GraphNode] = {}
    rels: dict[str, GraphRelationship] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            kind = value.get("_type")
            if kind == "node":
                nodes[value["id"]] = GraphNode(
                    id=value["id"],
                    labels=value.get("labels", []),
                    properties=value.get("properties", {}),
                )
                return
            if kind == "relationship":
                if value.get("start") and value.get("end"):
                    rels[value["id"]] = GraphRelationship(
                        id=value["id"],
                        type=value.get("rel_type", "RELATED_TO"),
                        start=value["start"],
                        end=value["end"],
                        properties=value.get("properties", {}),
                    )
                return
            if kind == "path":
                for item in value.get("nodes", []):
                    walk(item)
                for item in value.get("relationships", []):
                    walk(item)
                return
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for record in records:
        walk(record)

    return list(nodes.values()), list(rels.values())


class Neo4jClient:
    """Thin async wrapper around the Neo4j driver with lazy connection."""

    def __init__(self) -> None:
        self._driver: AsyncDriver | None = None
        self._lock = asyncio.Lock()
        self._unavailable_reason: str | None = None

    async def driver(self) -> AsyncDriver:
        if self._driver is not None:
            return self._driver

        async with self._lock:
            if self._driver is not None:
                return self._driver
            if not settings.neo4j_password:
                raise GraphUnavailableError(
                    "NEO4J_PASSWORD is not set; knowledge graph is disabled."
                )
            self._driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_username, settings.neo4j_password),
                max_connection_pool_size=settings.neo4j_max_connection_pool_size,
                connection_timeout=settings.neo4j_connection_timeout,
            )
            return self._driver

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def health(self) -> dict[str, Any]:
        try:
            driver = await self.driver()
            await driver.verify_connectivity()
        except GraphUnavailableError as exc:
            return {"ok": False, "reason": str(exc), "configured": False}
        except (ServiceUnavailable, Neo4jError, OSError) as exc:
            return {"ok": False, "reason": str(exc)[:300], "configured": True}
        return {
            "ok": True,
            "configured": True,
            "uri": settings.neo4j_uri,
            "database": settings.neo4j_database,
        }

    async def run(
        self,
        cypher: str,
        parameters: dict[str, Any] | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a read query and return plain-Python records."""

        driver = await self.driver()
        cap = limit or settings.neo4j_max_rows

        try:
            async with driver.session(database=settings.neo4j_database) as session:
                result = await session.run(  # type: ignore[arg-type]
                    cypher,
                    parameters or {},
                    timeout=settings.neo4j_query_timeout,
                )
                records: list[dict[str, Any]] = []
                async for record in result:
                    records.append({k: to_python(v) for k, v in record.items()})
                    if len(records) >= cap:
                        break
                return records
        except (ServiceUnavailable, OSError) as exc:
            raise GraphUnavailableError(f"Neo4j unreachable: {exc}") from exc

    async def try_run(
        self, cypher: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Run a query, swallowing errors — used for optional introspection."""

        try:
            return await self.run(cypher, parameters)
        except (GraphUnavailableError, Neo4jError) as exc:
            logger.debug(
                "Optional Cypher failed",
                extra={"cypher": cypher[:120], "error": str(exc)[:200]},
            )
            return []


_client = Neo4jClient()


def get_kg_client() -> Neo4jClient:
    return _client
