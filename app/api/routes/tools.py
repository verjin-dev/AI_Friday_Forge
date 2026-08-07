from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.core.models import ToolCall, ToolResult
from app.mcp.client import get_mcp_manager
from app.mcp.registry import get_registry


router = APIRouter(prefix="/api/tools", tags=["tools"])


class ToolExecuteRequest(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
    role: str | None = None


@router.get("")
async def list_tools(role: str | None = Query(default=None)) -> dict:
    registry = get_registry()
    specs = registry.available_for(role) if role else registry.specs()
    return {
        "mcp_servers": get_mcp_manager().connected_servers,
        "tools": [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
                "server": spec.server,
                "external": spec.external,
                "tags": spec.tags,
            }
            for spec in specs
        ],
    }


@router.post("/execute", response_model=ToolResult)
async def execute(request: ToolExecuteRequest) -> ToolResult:
    """Direct tool execution for operators — RBAC still applies."""

    return await get_registry().execute(
        ToolCall(tool=request.tool, arguments=request.arguments),
        role=request.role,
    )
