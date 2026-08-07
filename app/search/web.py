from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.models import SearchResult


logger = get_logger(__name__)


def _normalise(row: dict[str, Any], index: int, total: int) -> SearchResult:
    title = row.get("title") or row.get("heading") or "Untitled result"
    body = row.get("body") or row.get("snippet") or row.get("description") or ""
    url = row.get("href") or row.get("url") or row.get("link")
    return SearchResult(
        title=str(title)[:200],
        snippet=str(body)[:800],
        source=str(url or "web"),
        url=str(url) if url else None,
        # Rank-based score: results arrive already ordered by the provider.
        score=round(1.0 - (index / max(total, 1)) * 0.6, 3),
        origin="web",
    )


def _search_sync(query: str, limit: int) -> list[SearchResult]:
    try:
        from ddgs import DDGS
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning("ddgs is not installed; web search disabled")
        return []

    try:
        with DDGS(verify=settings.search_verify_ssl) as client:
            rows = list(client.text(query, max_results=limit))
    except TypeError:
        # Older/newer ddgs builds differ on constructor and argument names.
        try:
            with DDGS() as client:
                rows = list(client.text(query=query, max_results=limit))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Web search failed", extra={"error": str(exc)[:200]})
            return []
    except Exception as exc:  # noqa: BLE001 - provider errors are non-fatal
        logger.warning("Web search failed", extra={"error": str(exc)[:200]})
        return []

    total = len(rows)
    return [_normalise(row, index, total) for index, row in enumerate(rows)]


async def web_search(query: str, *, limit: int | None = None) -> list[SearchResult]:
    """External context lookup (weather advisories, port notices, regulations).

    Runs the blocking provider client in a worker thread so the event loop keeps
    serving other agents.
    """

    if settings.search_api_provider == "none" or not query.strip():
        return []

    cap = limit or settings.search_result_limit
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_search_sync, query, cap), timeout=20.0
        )
    except asyncio.TimeoutError:
        logger.warning("Web search timed out", extra={"query": query[:120]})
        return []
