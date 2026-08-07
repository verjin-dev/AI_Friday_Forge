from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.kg.client import get_kg_client
from app.kg.introspect import get_graph_schema
from app.mcp.client import get_mcp_manager
from app.mcp.registry import get_registry


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness plus dependency status — safe to expose to the UI."""

    graph = await get_kg_client().health()
    schema = await get_graph_schema()

    return {
        "status": "ok",
        "app": settings.app_name,
        "tagline": settings.app_tagline,
        "environment": settings.environment,
        "domain": settings.platform_domain,
        "llm": {
            "configured": settings.llm_configured,
            "model": settings.openai_model,
            "base_url": settings.openai_base_url,
            "streaming": settings.llm_streaming,
        },
        "knowledge_graph": {
            **graph,
            "schema_source": schema.source,
            "labels": len(schema.labels),
            "relationship_types": len(schema.relationship_types),
            "node_count": schema.node_count,
        },
        "tools": {
            "registered": len(get_registry().names()),
            "mcp_servers": get_mcp_manager().connected_servers,
        },
        "observability": {
            "langsmith_tracing": settings.langsmith_tracing,
            "project": settings.langsmith_project,
        },
        "workflow": {
            "max_reflection_loops": settings.workflow_max_reflection_loops,
            "confidence_threshold": settings.workflow_confidence_threshold,
            "optimization_enabled": settings.workflow_enable_optimization,
            "human_in_the_loop": settings.workflow_human_in_the_loop,
        },
    }


@router.get("/ready")
async def ready() -> dict:
    graph = await get_kg_client().health()
    return {"ready": settings.llm_configured, "graph_ok": graph.get("ok", False)}
