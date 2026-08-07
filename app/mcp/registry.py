from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.core.models import ToolCall, ToolResult
from app.security.rbac import resolve_role


logger = get_logger(__name__)

Handler = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class ToolSpec:
    """A tool the Planner may select and the Tool Agent may execute."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    server: str = "builtin"
    #: Tools that reach outside the enterprise boundary are flagged so the
    #: Security Agent can reason about egress.
    external: bool = False
    tags: list[str] = field(default_factory=list)

    def to_prompt_line(self) -> str:
        args = ", ".join(self.parameters.get("properties", {}).keys())
        scope = "external" if self.external else "internal"
        return f"- {self.name}({args}) [{self.server}/{scope}]: {self.description}"


class ToolRegistry:
    """Central catalogue of built-in and MCP-provided tools."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec, *, replace: bool = False) -> None:
        if spec.name in self._specs and not replace:
            logger.debug("Tool already registered", extra={"tool": spec.name})
            return
        self._specs[spec.name] = spec

    def unregister(self, name: str) -> None:
        self._specs.pop(name, None)

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def specs(self) -> list[ToolSpec]:
        return [self._specs[name] for name in sorted(self._specs)]

    def available_for(self, role: str | None) -> list[ToolSpec]:
        allowed = resolve_role(role).tools
        return [spec for spec in self.specs() if spec.name in allowed]

    def catalogue_prompt(self, role: str | None = None) -> str:
        specs = self.available_for(role) if role else self.specs()
        if not specs:
            return "(no tools available for this role)"
        return "\n".join(spec.to_prompt_line() for spec in specs)

    async def execute(self, call: ToolCall, *, role: str | None = None) -> ToolResult:
        """Run one tool with RBAC, timeout and latency accounting."""

        started = time.perf_counter()
        spec = self._specs.get(call.tool)

        if spec is None:
            return ToolResult(
                tool=call.tool,
                ok=False,
                error=f"Unknown tool '{call.tool}'.",
                latency_ms=0.0,
            )

        if role is not None and spec.name not in resolve_role(role).tools:
            return ToolResult(
                tool=call.tool,
                server=spec.server,
                ok=False,
                error=(
                    f"Role '{resolve_role(role).name}' is not permitted to use "
                    f"'{spec.name}'."
                ),
                latency_ms=0.0,
            )

        try:
            output = await asyncio.wait_for(
                spec.handler(**call.arguments),
                timeout=settings.mcp_tool_timeout_seconds,
            )
            return ToolResult(
                tool=spec.name,
                server=spec.server,
                ok=True,
                output=output,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except asyncio.TimeoutError:
            return ToolResult(
                tool=spec.name,
                server=spec.server,
                ok=False,
                error=f"Timed out after {settings.mcp_tool_timeout_seconds}s.",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except TypeError as exc:
            return ToolResult(
                tool=spec.name,
                server=spec.server,
                ok=False,
                error=f"Invalid arguments: {exc}",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except Exception as exc:  # noqa: BLE001 - tool failures must not kill the run
            logger.warning(
                "Tool execution failed",
                extra={"tool": spec.name, "error": str(exc)[:300]},
            )
            return ToolResult(
                tool=spec.name,
                server=spec.server,
                ok=False,
                error=str(exc)[:500],
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )

    async def execute_many(
        self, calls: list[ToolCall], *, role: str | None = None
    ) -> list[ToolResult]:
        """Parallel tool execution — the Planner emits independent calls."""

        if not calls:
            return []
        return list(
            await asyncio.gather(*(self.execute(call, role=role) for call in calls))
        )


_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _registry
