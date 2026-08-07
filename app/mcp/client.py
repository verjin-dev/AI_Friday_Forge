from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.mcp.registry import ToolSpec, get_registry
from app.security.rbac import ROLES


logger = get_logger(__name__)

#: Roles that inherit newly discovered MCP tools unless the config says otherwise.
_DEFAULT_MCP_ROLES = ("admin", "ops_manager")


class MCPManager:
    """Connects to configured MCP servers and publishes their tools.

    Servers are declared in ``mcp_servers.json`` using the standard shape::

        {"mcpServers": {"name": {"command": "npx", "args": [...], "env": {}}}}

    Connection failures are logged and skipped — the platform keeps running on
    its built-in tools.
    """

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._sessions: dict[str, Any] = {}
        self._connected = False

    @property
    def connected_servers(self) -> list[str]:
        return sorted(self._sessions)

    def _load_config(self) -> dict[str, dict[str, Any]]:
        path = settings.mcp_config_path
        if not path.exists():
            logger.info("No MCP server config found", extra={"path": str(path)})
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Invalid MCP config", extra={"error": str(exc)[:200]})
            return {}
        servers = payload.get("mcpServers") or payload.get("servers") or {}
        return servers if isinstance(servers, dict) else {}

    async def connect_all(self) -> None:
        if not settings.mcp_enabled or self._connected:
            return

        servers = self._load_config()
        if not servers:
            self._connected = True
            return

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:  # pragma: no cover - optional dependency
            logger.warning("mcp package not installed; external MCP servers skipped")
            self._connected = True
            return

        for name, config in servers.items():
            command = config.get("command")
            if not command:
                logger.warning("MCP server missing command", extra={"server": name})
                continue
            try:
                params = StdioServerParameters(
                    command=command,
                    args=config.get("args") or [],
                    env=config.get("env") or None,
                )
                read, write = await self._stack.enter_async_context(
                    stdio_client(params)
                )
                session = await self._stack.enter_async_context(
                    ClientSession(read, write)
                )
                await session.initialize()
                self._sessions[name] = session
                await self._register_server_tools(name, session, config)
            except Exception as exc:  # noqa: BLE001 - one bad server must not block startup
                logger.error(
                    "Failed to connect MCP server",
                    extra={"server": name, "error": str(exc)[:300]},
                )

        self._connected = True

    async def _register_server_tools(
        self, server: str, session: Any, config: dict[str, Any]
    ) -> None:
        listing = await session.list_tools()
        registry = get_registry()
        roles = config.get("roles") or list(_DEFAULT_MCP_ROLES)

        for tool in listing.tools:
            qualified = f"{server}__{tool.name}"

            def make_handler(tool_name: str, active: Any):
                async def handler(**kwargs: Any) -> Any:
                    result = await active.call_tool(tool_name, kwargs)
                    parts: list[Any] = []
                    for item in getattr(result, "content", []) or []:
                        text = getattr(item, "text", None)
                        parts.append(text if text is not None else str(item))
                    return parts if len(parts) != 1 else parts[0]

                return handler

            registry.register(
                ToolSpec(
                    name=qualified,
                    description=(tool.description or f"MCP tool '{tool.name}'")[:400],
                    parameters=getattr(tool, "inputSchema", None)
                    or {"type": "object", "properties": {}},
                    handler=make_handler(tool.name, session),
                    server=server,
                    external=True,
                    tags=["mcp"],
                ),
                replace=True,
            )

            for role_name in roles:
                role = ROLES.get(str(role_name).lower())
                if role is not None:
                    role.tools.add(qualified)

        logger.info(
            "MCP server connected",
            extra={"server": server, "tools": len(listing.tools)},
        )

    async def close(self) -> None:
        if not self._connected:
            return
        try:
            await self._stack.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP shutdown error", extra={"error": str(exc)[:200]})
        finally:
            self._sessions.clear()
            self._connected = False


_manager = MCPManager()


def get_mcp_manager() -> MCPManager:
    return _manager
