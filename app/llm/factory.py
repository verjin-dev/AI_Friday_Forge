from __future__ import annotations

from functools import lru_cache

import httpx
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.logging import get_logger


logger = get_logger(__name__)


class LLMNotConfiguredError(RuntimeError):
    """Raised when an agent needs the LLM but no API key is present."""


def build_chat_llm(
    *,
    temperature: float | None = None,
    streaming: bool | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    """Construct a ChatOpenAI bound to the configured enterprise gateway.

    Corporate gateways frequently terminate TLS with an internal CA, so the
    httpx clients are built explicitly with a verification toggle rather than
    relying on the SDK defaults.
    """

    if not settings.openai_api_key:
        raise LLMNotConfiguredError(
            "OPENAI_API_KEY is required to call the configured LLM."
        )

    verify = settings.openai_verify_ssl
    if not verify:
        logger.warning(
            "TLS verification disabled for LLM gateway",
            extra={"base_url": settings.openai_base_url},
        )

    sync_client = httpx.Client(verify=verify, timeout=timeout or settings.llm_timeout_seconds)
    async_client = httpx.AsyncClient(
        verify=verify, timeout=timeout or settings.llm_timeout_seconds
    )

    return ChatOpenAI(
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        temperature=settings.llm_temperature if temperature is None else temperature,
        streaming=settings.llm_streaming if streaming is None else streaming,
        timeout=timeout or settings.llm_timeout_seconds,
        max_retries=(
            settings.llm_max_retries if max_retries is None else max_retries
        ),
        max_tokens=max_tokens or settings.llm_max_tokens,
        http_client=sync_client,
        http_async_client=async_client,
    )


@lru_cache(maxsize=8)
def _cached_llm(temperature: float, streaming: bool) -> ChatOpenAI:
    return build_chat_llm(temperature=temperature, streaming=streaming)


def get_chat_llm(
    *, temperature: float | None = None, streaming: bool | None = None
) -> ChatOpenAI:
    """Shared client for agent calls — avoids rebuilding httpx pools per node."""

    return _cached_llm(
        settings.llm_temperature if temperature is None else temperature,
        settings.llm_streaming if streaming is None else streaming,
    )
