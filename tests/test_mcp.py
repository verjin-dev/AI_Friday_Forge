import pytest
from app.mcp.builtin import register_builtin_tools, BUILTIN_SPECS
from app.mcp.registry import get_registry
from app.security.rbac import resolve_role, allowed_tools_for


def test_builtin_mcp_tools_registered():
    register_builtin_tools()
    registry = get_registry()

    # Verify at least 18 built-in tools are registered
    assert len(registry.specs()) >= 18

    # Verify key logistics tools are present
    registered_names = registry.names()
    assert "graph_schema" in registered_names
    assert "graph_query" in registered_names
    assert "route_plan" in registered_names
    assert "replan_route" in registered_names
    assert "route_monitor_start" in registered_names
    assert "route_monitor_status" in registered_names
    assert "route_monitor_poll" in registered_names
    assert "evaluate_constraints" in registered_names
    assert "algorithm_list" in registered_names
    assert "network_status" in registered_names


@pytest.mark.asyncio
async def test_execute_algorithm_list_tool():
    register_builtin_tools()
    registry = get_registry()

    spec = registry.get("algorithm_list")
    assert spec is not None

    res = await spec.handler()
    assert "available_algorithms" in res
    assert "astar" in res["available_algorithms"]
    assert "yen" in res["available_algorithms"]


@pytest.mark.asyncio
async def test_execute_route_monitor_status_tool():
    register_builtin_tools()
    registry = get_registry()

    spec = registry.get("route_monitor_status")
    assert spec is not None

    res = await spec.handler()
    assert "active_count" in res


def test_rbac_permits_new_tools():
    admin_tools = allowed_tools_for("admin")
    ops_tools = allowed_tools_for("ops_manager")

    for tool in [
        "replan_route",
        "route_monitor_start",
        "route_monitor_status",
        "route_monitor_poll",
        "evaluate_constraints",
        "algorithm_list",
    ]:
        assert tool in admin_tools
        assert tool in ops_tools


def test_mcp_server_module_import():
    from app.mcp.server import mcp
    assert mcp.name == "LogiPilot AI Engine"
