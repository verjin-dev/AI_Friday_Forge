from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.kg.client import get_kg_client
from app.kg.introspect import get_graph_schema
from app.mcp.builtin import register_builtin_tools
from app.mcp.client import get_mcp_manager
from app.observability.tracing import configure_langsmith
from app.workflow.graph import get_workflow


logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    settings.ensure_data_dirs()
    configure_langsmith()

    register_builtin_tools()
    await get_mcp_manager().connect_all()

    # Warm the schema cache and compile the graph so the first request is fast.
    schema = await get_graph_schema()
    get_workflow()

    logger.info(
        "Platform ready",
        extra={
            "domain": settings.platform_domain,
            "graph_source": schema.source,
            "graph_labels": len(schema.labels),
            "llm_configured": settings.llm_configured,
        },
    )

    yield

    await get_mcp_manager().close()
    await get_kg_client().close()
    logger.info("Platform shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=(
            f"{settings.app_tagline}. Multi-agent platform for enterprise problem "
            "solving: intelligent planning, knowledge graph reasoning, secure tool "
            "orchestration, constraint-checked route optimisation and explainable "
            "output."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Imported here so route modules can rely on settings being loaded.
    from app.api.routes import (
        chat,
        constraints,
        fleet,
        graph,
        health,
        observability,
        routing,
        tools,
    )

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(routing.router)
    app.include_router(fleet.router)
    app.include_router(graph.router)
    app.include_router(tools.router)
    app.include_router(constraints.router)
    app.include_router(observability.router)

    return app


app = create_app()
