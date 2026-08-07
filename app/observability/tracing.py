from __future__ import annotations

import os

from app.core.config import settings
from app.core.logging import get_logger


logger = get_logger(__name__)


def configure_langsmith() -> bool:
    """Enable LangSmith tracing for every LangChain/LangGraph call.

    LangChain reads these from the environment, so this must run before the
    workflow is compiled. Returns whether tracing is active.
    """

    if not settings.langsmith_tracing:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ.pop("LANGSMITH_TRACING", None)
        logger.info("LangSmith tracing disabled")
        return False

    if not settings.langsmith_api_key:
        logger.warning(
            "LANGSMITH_TRACING is on but LANGSMITH_API_KEY is missing; "
            "tracing stays disabled"
        )
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        return False

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_ENDPOINT"] = settings.langsmith_endpoint
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project

    logger.info(
        "LangSmith tracing enabled", extra={"project": settings.langsmith_project}
    )
    return True
