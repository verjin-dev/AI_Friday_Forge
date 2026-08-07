from __future__ import annotations

import asyncio
import re

from app.agents.base import AgentOutcome, BaseAgent
from app.core.models import AgentName, AgentStatus, GraphContext
from app.core.state import PlatformState, merge_graph_context
from app.kg.client import GraphUnavailableError, collect_graph_elements, get_kg_client
from app.kg.cypher import generate_cypher
from app.kg.introspect import get_graph_schema
from app.kg.traversal import (
    dependency_analysis,
    discover_entities,
    expand_neighbourhood,
    historical_context,
    impact_analysis,
    summarise_context,
)
from app.llm.structured import LLMUsage
from app.security.injection import scan_retrieved_content


#: Identifier-looking tokens: SHP-10231, ORD_5567, VH12, INC-9.
_ID_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]{1,}[-_/]?\d{2,}\b")
_QUOTED = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{2,60})[\"'“”‘’]")
_PROPER = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b")

_STOP_PROPER = {
    "What", "Which", "Why", "How", "When", "Where", "Who", "The", "This",
    "That", "Please", "Show", "List", "Find", "Give", "Can", "Should", "Does",
}

#: Intent categories that warrant an impact / blast-radius walk.
_IMPACT_HINTS = ("impact", "risk", "affect", "downstream", "blast", "disrupt", "fail")
_DEPENDENCY_HINTS = ("depend", "root cause", "rca", "why", "upstream", "cause")
_HISTORY_HINTS = ("history", "historical", "previous", "past", "recurring", "again")


def extract_entity_terms(question: str, planned: list[str]) -> list[str]:
    """Pull likely entity references out of the question, cheaply."""

    terms: list[str] = [term.strip() for term in planned if term and term.strip()]
    terms.extend(_QUOTED.findall(question))
    terms.extend(_ID_TOKEN.findall(question))
    terms.extend(
        match
        for match in _PROPER.findall(question)
        if match.split()[0] not in _STOP_PROPER
    )

    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        key = term.lower()
        if key not in seen and len(term) >= 2:
            seen.add(key)
            unique.append(term)
    return unique[:10]


class KnowledgeAgent(BaseAgent):
    """Neo4j reasoning: entity discovery, traversal, dependency and impact."""

    name = AgentName.KNOWLEDGE

    async def run(self, state: PlatformState) -> AgentOutcome:
        question = state["question"]
        plan = state.get("plan")
        schema = await get_graph_schema()
        usage = LLMUsage()
        queries_run = 0

        if schema.source != "live":
            return AgentOutcome(
                updates={
                    "graph_context": GraphContext(
                        note=(
                            "Knowledge graph is unreachable or empty — reasoning "
                            "will proceed on documents and external context only."
                        )
                    )
                },
                summary="Graph unavailable; no entities retrieved.",
                status=AgentStatus.SKIPPED,
                detail={"schema_source": schema.source},
            )

        planned_entities = list(plan.intent.entities) if plan else []
        terms = extract_entity_terms(question, planned_entities)

        # 1. Entity discovery
        discovered = await discover_entities(terms, schema)
        queries_run += 1
        context = discovered
        seed_ids = [node.id for node in discovered.nodes][:15]

        # 2. Generated Cypher for the specific question, in parallel with
        #    structural expansion of whatever we already found.
        cypher_plan, cypher_usage = await generate_cypher(
            question, schema, entities=terms
        )
        usage.add(cypher_usage)

        client = get_kg_client()
        tasks = [
            self._run_query(client, query.cypher, query.purpose)
            for query in cypher_plan.queries
        ]

        category = (plan.intent.category if plan else "").lower() + " " + question.lower()
        if seed_ids:
            tasks.append(expand_neighbourhood(seed_ids, hops=2))
            if any(hint in category for hint in _DEPENDENCY_HINTS):
                tasks.append(dependency_analysis(seed_ids, schema))
            if any(hint in category for hint in _IMPACT_HINTS):
                tasks.append(impact_analysis(seed_ids, schema))
            if any(hint in category for hint in _HISTORY_HINTS):
                tasks.append(historical_context(seed_ids))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                self.log.warning(
                    "Graph branch failed", extra={"error": str(result)[:200]}
                )
                continue
            queries_run += 1
            context = merge_graph_context(context, result)

        # 3. Indirect injection scan — graph content is data, never instructions.
        rendered = summarise_context(context)
        injection_findings = scan_retrieved_content(rendered, source="graph")
        if injection_findings:
            context.note = (
                (context.note or "")
                + " Retrieved content contained instruction-like text; it is "
                "treated strictly as data."
            ).strip()

        summary = (
            f"{len(context.nodes)} entities, {len(context.relationships)} "
            f"relationships across {queries_run} query/queries"
        )
        if context.is_empty:
            summary = "No matching entities found in the knowledge graph."

        return AgentOutcome(
            updates={"graph_context": context},
            summary=summary,
            detail={
                "terms": terms,
                "cypher": context.cypher[:6],
                "node_count": len(context.nodes),
                "relationship_count": len(context.relationships),
                "hops": context.hops,
                "injection_findings": [f.check for f in injection_findings],
                "cypher_note": cypher_plan.note,
            },
            usage=usage,
            graph_queries=queries_run,
        )

    async def _run_query(self, client, cypher: str, purpose: str) -> GraphContext:
        try:
            records = await client.run(cypher)
        except GraphUnavailableError as exc:
            self.log.warning("Graph query failed", extra={"error": str(exc)[:200]})
            return GraphContext()
        nodes, rels = collect_graph_elements(records)
        return GraphContext(
            nodes=nodes,
            relationships=rels,
            cypher=[cypher],
            records=records if not nodes else records[:20],
            note=purpose,
        )
