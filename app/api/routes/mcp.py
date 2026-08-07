from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.models import ToolCall, ToolResult
from app.mcp.client import get_mcp_manager
from app.mcp.registry import get_registry


router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class MCPCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    role: str | None = None


@router.get("/tools")
async def list_mcp_tools(role: str | None = Query(default=None)) -> dict[str, Any]:
    """Return all tools registered in the platform's MCP ToolRegistry."""
    registry = get_registry()
    specs = registry.available_for(role) if role else registry.specs()
    return {
        "mcp_version": "1.0.0",
        "connected_mcp_servers": get_mcp_manager().connected_servers,
        "tool_count": len(specs),
        "tools": [
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": spec.parameters,
                "server": spec.server,
                "external": spec.external,
                "tags": spec.tags,
            }
            for spec in specs
        ],
    }


@router.post("/call", response_model=ToolResult)
async def call_mcp_tool(request: MCPCallRequest) -> ToolResult:
    """Execute any registered MCP tool via HTTP JSON-RPC."""
    return await get_registry().execute(
        ToolCall(tool=request.name, arguments=request.arguments),
        role=request.role,
    )
