from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.core.models import GraphSchema
from app.kg.ontology import ontology_for_domain, value_hints
from app.llm.structured import LLMUsage, structured_call


logger = get_logger(__name__)


class UnsafeCypherError(ValueError):
    """Raised when a generated query would mutate the graph."""


#: Clauses that write, mutate schema, or reach outside the database.
_WRITE_KEYWORDS = (
    "create",
    "merge",
    "delete",
    "detach",
    "set",
    "remove",
    "drop",
    "foreach",
    "load csv",
    "call db.create",
    "call apoc.create",
    "call apoc.merge",
    "call apoc.refactor",
    "call apoc.periodic",
    "call apoc.load",
    "call apoc.trigger",
    "call dbms",
    "grant",
    "revoke",
    "alter",
    "start database",
    "stop database",
    "use system",
)

_STRING_LITERAL = re.compile(r"'[^']*'|\"[^\"]*\"")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_LIMIT = re.compile(r"\blimit\s+\d+", re.IGNORECASE)


def _normalise(cypher: str) -> str:
    stripped = _COMMENT.sub(" ", cypher)
    stripped = _STRING_LITERAL.sub("''", stripped)
    return re.sub(r"\s+", " ", stripped).strip().lower()


def assert_read_only(cypher: str) -> None:
    """Reject anything that could mutate the enterprise graph.

    The Knowledge Agent is a read path by design; write access belongs to
    governed ingestion pipelines, not to model-generated Cypher.
    """

    normalised = _normalise(cypher)
    if not normalised:
        raise UnsafeCypherError("empty query")

    if ";" in normalised.rstrip(";"):
        raise UnsafeCypherError("multiple statements are not allowed")

    for keyword in _WRITE_KEYWORDS:
        pattern = (
            rf"(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])"
            if " " not in keyword
            else re.escape(keyword)
        )
        if re.search(pattern, normalised):
            raise UnsafeCypherError(f"write or admin clause detected: '{keyword}'")


def enforce_limit(cypher: str, limit: int | None = None) -> str:
    """Append a LIMIT when the model forgot one, so a query can't run away."""

    cap = limit or settings.neo4j_max_rows
    body = cypher.strip().rstrip(";")
    if _LIMIT.search(_normalise(body)):
        return body
    return f"{body}\nLIMIT {cap}"


def sanitize(cypher: str, limit: int | None = None) -> str:
    assert_read_only(cypher)
    return enforce_limit(cypher, limit)


# ----------------------------------------------------------------------
# Natural language -> Cypher
# ----------------------------------------------------------------------
class CypherQuery(BaseModel):
    purpose: str = Field(description="What this query is meant to retrieve.")
    cypher: str = Field(description="A single read-only Cypher statement.")


class CypherPlan(BaseModel):
    queries: list[CypherQuery] = Field(default_factory=list)
    note: str | None = None


_SYSTEM = """You are the Knowledge Agent's Cypher planner for an enterprise \
{domain} knowledge graph.

Write read-only Cypher that answers the user's business question using ONLY the \
labels, relationship types and properties in the provided schema. Never invent \
labels or properties that are not listed.

Rules:
- Read-only. No CREATE, MERGE, SET, DELETE, REMOVE, DROP, LOAD CSV or admin calls.
- Return whole nodes and relationships (or paths) where possible so the graph can \
be visualised — e.g. `RETURN n, r, m` rather than only scalar properties.
- Prefer variable-length patterns for multi-hop reasoning, bounded to 3 hops \
(e.g. `-[*1..3]-`).
- Always include a LIMIT.
- Use case-insensitive matching for free-text lookups: \
`toLower(n.name) CONTAINS toLower($term)`.
- Emit 1 to 3 complementary queries: entity lookup first, then relationships, \
then aggregate or historical context.
- If the schema cannot answer the question, return an empty query list and \
explain why in `note`."""


async def generate_cypher(
    question: str,
    schema: GraphSchema,
    *,
    entities: list[str] | None = None,
) -> tuple[CypherPlan, LLMUsage]:
    """Turn a business question into a small set of safe read queries."""

    ontology = ontology_for_domain()
    context = [f"Graph schema (source: {schema.source}):", schema.to_prompt()]

    hints = value_hints()
    if hints:
        context.append("\nEnumerated property values (filter on these exact strings):")
        context.extend(f"  {hint}" for hint in hints)

    if entities:
        context.append(f"\nEntities detected in the question: {', '.join(entities)}")
    if schema.source != "live":
        context.append(
            "\nNOTE: the live graph is empty or unreachable, so this schema is the "
            "expected domain ontology. Queries may legitimately return no rows."
        )
    context.append(f"\nBusiness question: {question}")

    plan, usage = await structured_call(
        CypherPlan,
        system=_SYSTEM.format(domain=ontology.domain),
        user="\n".join(context),
        fallback=CypherPlan(queries=[], note="Cypher planning failed."),
    )

    safe: list[CypherQuery] = []
    for query in plan.queries:
        try:
            safe.append(
                CypherQuery(purpose=query.purpose, cypher=sanitize(query.cypher))
            )
        except UnsafeCypherError as exc:
            logger.warning(
                "Discarded generated Cypher",
                extra={"reason": str(exc), "cypher": query.cypher[:200]},
            )

    return CypherPlan(queries=safe, note=plan.note), usage
