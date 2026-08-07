from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.core.models import GraphSchema, SearchResult
from app.kg.client import get_kg_client
from app.search.web import web_search


logger = get_logger(__name__)

_TOKEN = re.compile(r"[a-z0-9]{2,}")
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "have", "has", "had", "what", "why", "how", "which", "when", "where", "who",
    "our", "their", "its", "you", "your", "all", "any", "can", "will", "into",
    "about", "there", "them", "then", "than", "been", "does", "did", "not",
}

_DOC_SUFFIXES = {".md", ".txt", ".json", ".csv", ".log", ".yaml", ".yml"}


def _tokenise(text: str) -> list[str]:
    return [
        token
        for token in _TOKEN.findall(text.lower())
        if token not in _STOPWORDS
    ]


# ----------------------------------------------------------------------
# Graph-backed search
# ----------------------------------------------------------------------
async def graph_fulltext_search(
    query: str, schema: GraphSchema, *, limit: int = 10
) -> list[SearchResult]:
    """Full-text search over the knowledge graph when an index exists."""

    if not schema.fulltext_indexes:
        return []

    client = get_kg_client()
    results: list[SearchResult] = []
    for index in schema.fulltext_indexes[:2]:
        rows = await client.try_run(
            "CALL db.index.fulltext.queryNodes($index, $query) "
            "YIELD node, score RETURN node, score ORDER BY score DESC LIMIT $limit",
            {"index": index, "query": query, "limit": limit},
        )
        for row in rows:
            node = row.get("node") or {}
            props = node.get("properties", {}) if isinstance(node, dict) else {}
            labels = node.get("labels", []) if isinstance(node, dict) else []
            title = str(
                props.get("name")
                or props.get("title")
                or props.get("id")
                or "/".join(labels)
                or "Graph entity"
            )
            snippet = ", ".join(
                f"{key}={value}" for key, value in list(props.items())[:8]
            )
            results.append(
                SearchResult(
                    title=title[:200],
                    snippet=snippet[:800],
                    source=f"neo4j:{index}:{'/'.join(labels)}",
                    score=float(row.get("score") or 0.0),
                    origin="graph",
                )
            )
    return results


async def graph_metadata_search(
    query: str, schema: GraphSchema, *, limit: int = 10
) -> list[SearchResult]:
    """Metadata search — match query terms against node identifying properties."""

    terms = _tokenise(query)[:6]
    if not terms or not schema.labels:
        return []

    cypher = (
        "UNWIND $terms AS term "
        "MATCH (n) "
        "WHERE any(k IN keys(n) WHERE "
        "  n[k] IS NOT NULL AND toLower(toString(n[k])) CONTAINS term) "
        "RETURN DISTINCT n, labels(n) AS labels LIMIT $limit"
    )
    rows = await get_kg_client().try_run(cypher, {"terms": terms, "limit": limit})

    results: list[SearchResult] = []
    for row in rows:
        node = row.get("n") or {}
        props = node.get("properties", {}) if isinstance(node, dict) else {}
        labels = row.get("labels") or []
        title = str(
            props.get("name") or props.get("title") or props.get("id") or "Entity"
        )
        overlap = len(
            set(terms) & set(_tokenise(" ".join(str(v) for v in props.values())))
        )
        results.append(
            SearchResult(
                title=title[:200],
                snippet=", ".join(
                    f"{k}={v}" for k, v in list(props.items())[:8]
                )[:800],
                source=f"neo4j:{'/'.join(labels)}",
                score=round(overlap / max(len(terms), 1), 3),
                origin="metadata",
            )
        )
    return results


# ----------------------------------------------------------------------
# Document search
# ----------------------------------------------------------------------
def _score_document(path: Path, terms: Counter[str]) -> tuple[float, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0.0, ""

    tokens = Counter(_tokenise(text))
    if not tokens:
        return 0.0, ""

    score = 0.0
    for term, weight in terms.items():
        if tokens[term]:
            # Sub-linear term frequency keeps long documents from dominating.
            score += weight * (1 + math.log(tokens[term]))

    snippet = ""
    if score:
        lowered = text.lower()
        for term in terms:
            position = lowered.find(term)
            if position != -1:
                start = max(0, position - 120)
                snippet = text[start : start + 400].replace("\n", " ").strip()
                break
    return score, snippet


def _document_search_sync(query: str, limit: int) -> list[SearchResult]:
    root = settings.mcp_filesystem_root
    if not root.exists():
        return []

    terms = Counter(_tokenise(query))
    if not terms:
        return []

    scored: list[tuple[float, Path, str]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _DOC_SUFFIXES:
            continue
        if path.stat().st_size > 2_000_000:
            continue
        score, snippet = _score_document(path, terms)
        if score > 0:
            scored.append((score, path, snippet))

    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[:limit]
    ceiling = top[0][0] if top else 1.0
    return [
        SearchResult(
            title=path.name,
            snippet=snippet[:800],
            source=str(path.relative_to(root)),
            score=round(score / ceiling, 3) if ceiling else 0.0,
            origin="document",
        )
        for score, path, snippet in top
    ]


async def document_search(query: str, *, limit: int = 5) -> list[SearchResult]:
    """Keyword search over the enterprise document drop directory."""

    return await asyncio.to_thread(_document_search_sync, query, limit)


# ----------------------------------------------------------------------
# Hybrid orchestration
# ----------------------------------------------------------------------
def _dedupe_and_rank(
    groups: list[list[SearchResult]], *, limit: int
) -> list[SearchResult]:
    """Reciprocal-rank fusion across heterogeneous result sets."""

    fused: dict[str, tuple[float, SearchResult]] = {}
    for group in groups:
        for rank, result in enumerate(group):
            key = (result.url or f"{result.source}|{result.title}").lower()
            contribution = 1.0 / (60 + rank + 1)
            if key in fused:
                score, existing = fused[key]
                fused[key] = (score + contribution, existing)
            else:
                fused[key] = (contribution, result)

    ordered = sorted(fused.values(), key=lambda item: item[0], reverse=True)
    ceiling = ordered[0][0] if ordered else 1.0
    output: list[SearchResult] = []
    for score, result in ordered[:limit]:
        result.score = round(score / ceiling, 3) if ceiling else 0.0
        output.append(result)
    return output


async def hybrid_search(
    query: str,
    schema: GraphSchema,
    *,
    include_web: bool = True,
    limit: int | None = None,
) -> list[SearchResult]:
    """Semantic-ish + full-text + metadata + document + web, fused and ranked."""

    cap = limit or max(settings.search_result_limit, 6)

    tasks = [
        graph_fulltext_search(query, schema, limit=cap),
        graph_metadata_search(query, schema, limit=cap),
        document_search(query, limit=cap),
    ]
    if include_web and settings.search_api_provider != "none":
        tasks.append(web_search(query, limit=settings.search_result_limit))

    groups = await asyncio.gather(*tasks, return_exceptions=True)

    clean: list[list[SearchResult]] = []
    for group in groups:
        if isinstance(group, BaseException):
            logger.warning("Search branch failed", extra={"error": str(group)[:200]})
            continue
        clean.append(group)

    return _dedupe_and_rank(clean, limit=cap)
