from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, TypeVar

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.factory import get_chat_llm


logger = get_logger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(slots=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def add(self, other: "LLMUsage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.calls += other.calls

    @property
    def cost_usd(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * settings.cost_per_million_input_tokens
            + self.completion_tokens
            / 1_000_000
            * settings.cost_per_million_output_tokens
        )


def _usage_from(message: BaseMessage) -> LLMUsage:
    meta = getattr(message, "usage_metadata", None) or {}
    if meta:
        return LLMUsage(
            prompt_tokens=int(meta.get("input_tokens", 0) or 0),
            completion_tokens=int(meta.get("output_tokens", 0) or 0),
            calls=1,
        )
    token_usage = (getattr(message, "response_metadata", None) or {}).get(
        "token_usage", {}
    )
    return LLMUsage(
        prompt_tokens=int(token_usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(token_usage.get("completion_tokens", 0) or 0),
        calls=1,
    )


def _build_messages(
    system: str, user: str, history: list[dict[str, str]] | None = None
) -> list[BaseMessage]:
    messages: list[BaseMessage] = [SystemMessage(content=system)]
    for turn in history or []:
        role = (turn.get("role") or "user").lower()
        content = turn.get("content") or ""
        if not content:
            continue
        messages.append(
            AIMessage(content=content)
            if role in {"assistant", "ai"}
            else HumanMessage(content=content)
        )
    messages.append(HumanMessage(content=user))
    return messages


async def _invoke(messages: list[BaseMessage], temperature: float | None) -> AIMessage:
    """Invoke the gateway, tolerating models that reject a custom temperature."""

    try:
        llm = get_chat_llm(temperature=temperature, streaming=False)
        return await llm.ainvoke(messages)
    except Exception as exc:  # noqa: BLE001 - gateway errors vary by vendor
        text = str(exc).lower()
        retryable_param = "temperature" in text or "unsupported_value" in text
        if not retryable_param:
            raise
        logger.warning(
            "Gateway rejected temperature; retrying with model default",
            extra={"error": str(exc)[:300]},
        )
        llm = get_chat_llm(temperature=1.0, streaming=False)
        return await llm.ainvoke(messages)


def _extract_json(raw: str) -> Any:
    """Pull a JSON object out of a model response that may be fenced or chatty."""

    candidates: list[str] = []
    fenced = _FENCE.findall(raw)
    candidates.extend(fenced)
    candidates.append(raw)

    for candidate in candidates:
        text = candidate.strip()
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Fall back to the outermost balanced object/array in the string.
        for opener, closer in (("{", "}"), ("[", "]")):
            start = text.find(opener)
            end = text.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
    raise ValueError("no JSON object found in model response")


async def structured_call(
    schema: type[TModel],
    *,
    system: str,
    user: str,
    history: list[dict[str, str]] | None = None,
    temperature: float | None = None,
    fallback: TModel | None = None,
) -> tuple[TModel, LLMUsage]:
    """Ask the model for JSON matching ``schema`` and parse it defensively.

    Enterprise gateways vary in their support for native structured output, so
    this uses schema-in-prompt plus one repair round-trip, which works on any
    OpenAI-compatible endpoint.
    """

    usage = LLMUsage()
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    system_prompt = (
        f"{system}\n\n"
        "Respond with a single JSON object only — no prose, no markdown fence.\n"
        "It must validate against this JSON Schema:\n"
        f"{schema_json}"
    )

    messages = _build_messages(system_prompt, user, history)
    last_error: str | None = None

    for attempt in range(2):
        try:
            response = await _invoke(messages, temperature)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "LLM call failed",
                extra={"schema": schema.__name__, "error": str(exc)[:300]},
            )
            if fallback is not None:
                return fallback, usage
            raise

        usage.add(_usage_from(response))
        raw = response.content if isinstance(response.content, str) else str(
            response.content
        )

        try:
            return schema.model_validate(_extract_json(raw)), usage
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)[:800]
            logger.warning(
                "Structured output did not validate",
                extra={
                    "schema": schema.__name__,
                    "attempt": attempt + 1,
                    "error": last_error,
                },
            )
            if attempt == 0:
                messages = [
                    *messages,
                    AIMessage(content=raw[:4000]),
                    HumanMessage(
                        content=(
                            "That response was not valid against the schema:\n"
                            f"{last_error}\n\n"
                            "Return corrected JSON only."
                        )
                    ),
                ]

    if fallback is not None:
        logger.warning(
            "Falling back to default payload",
            extra={"schema": schema.__name__, "error": last_error},
        )
        return fallback, usage
    raise ValueError(f"{schema.__name__} could not be parsed: {last_error}")


async def text_call(
    *,
    system: str,
    user: str,
    history: list[dict[str, str]] | None = None,
    temperature: float | None = None,
) -> tuple[str, LLMUsage]:
    """Plain natural-language completion with usage accounting."""

    usage = LLMUsage()
    response = await _invoke(_build_messages(system, user, history), temperature)
    usage.add(_usage_from(response))
    content = response.content
    return (content if isinstance(content, str) else str(content)), usage
