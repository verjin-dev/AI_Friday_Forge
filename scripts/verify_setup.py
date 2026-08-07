"""Check that a fresh environment is wired correctly.

Run this after following SETUP.md. It reports what is ready, what is missing,
and what is optional — without ever printing a secret.

    python scripts/verify_setup.py

Exit code is 0 when everything required is in place, 1 otherwise, so it can
also be used as a smoke check in CI.
"""

from __future__ import annotations

import asyncio
import importlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OK = "  [ok]  "
WARN = "  [--]  "
BAD = "  [XX]  "

failures: list[str] = []
warnings: list[str] = []


def ok(message: str) -> None:
    print(f"{OK}{message}")


def warn(message: str, hint: str = "") -> None:
    print(f"{WARN}{message}")
    if hint:
        print(f"         {hint}")
    warnings.append(message)


def bad(message: str, hint: str = "") -> None:
    print(f"{BAD}{message}")
    if hint:
        print(f"         {hint}")
    failures.append(message)


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


# ----------------------------------------------------------------------
def check_python() -> None:
    section("Python")
    version = sys.version_info
    if version >= (3, 11):
        ok(f"Python {version.major}.{version.minor}.{version.micro}")
    else:
        bad(
            f"Python {version.major}.{version.minor} is too old",
            "Python 3.11 or newer is required (3.12 is what this was built on).",
        )

    if sys.prefix == sys.base_prefix:
        warn(
            "Not running inside a virtual environment",
            "Recommended: python -m venv .venv, then activate it.",
        )
    else:
        ok(f"Virtual environment active ({Path(sys.prefix).name})")


REQUIRED_PACKAGES = [
    ("fastapi", "FastAPI"),
    ("uvicorn", "Uvicorn"),
    ("langgraph", "LangGraph"),
    ("langchain_openai", "LangChain OpenAI"),
    ("neo4j", "Neo4j driver"),
    ("pydantic_settings", "pydantic-settings"),
    ("httpx", "httpx"),
    ("sse_starlette", "sse-starlette"),
]

OPTIONAL_PACKAGES = [
    ("ddgs", "web search"),
    ("mcp", "external MCP servers"),
    ("pytest", "test suite"),
]


def check_packages() -> None:
    section("Python packages")
    missing = []
    for module, label in REQUIRED_PACKAGES:
        try:
            importlib.import_module(module)
            ok(label)
        except ImportError:
            missing.append(label)
            bad(f"{label} is not installed")
    if missing:
        print("         Fix with: python -m pip install -r requirements.txt")

    for module, purpose in OPTIONAL_PACKAGES:
        try:
            importlib.import_module(module)
            ok(f"{module} (optional — {purpose})")
        except ImportError:
            warn(f"{module} missing (optional — {purpose} will be unavailable)")


def check_env() -> None:
    section("Configuration (.env)")

    if not (ROOT / ".env").exists():
        bad(
            ".env not found",
            "Copy the template: cp .env.example .env, then fill it in.",
        )
        return
    ok(".env present")

    try:
        from app.core.config import settings
    except Exception as exc:  # noqa: BLE001
        bad(f"Could not load settings: {exc}")
        return

    if settings.neo4j_password:
        ok(f"NEO4J_PASSWORD set · uri {settings.neo4j_uri}")
    else:
        bad(
            "NEO4J_PASSWORD is empty",
            "The knowledge graph is disabled without it — routing will not work.",
        )

    if settings.neo4j_uri.startswith("neo4j://"):
        warn(
            "NEO4J_URI uses the neo4j:// scheme",
            "Use bolt:// for a standalone server; neo4j:// expects a cluster and "
            "fails with 'Unable to retrieve routing information'.",
        )

    if settings.llm_configured:
        ok(f"OPENAI_API_KEY set · model {settings.openai_model}")
    else:
        warn(
            "OPENAI_API_KEY is empty",
            "Deterministic routing still works; the 12-agent chat workflow will not.",
        )

    if settings.google_maps_enabled:
        ok("GOOGLE_MAPS_API_KEY set (live traffic available)")
    else:
        warn(
            "GOOGLE_MAPS_API_KEY is empty",
            "Delay prediction falls back to road-class estimates and says so.",
        )

    if not settings.openai_verify_ssl or not settings.external_verify_ssl:
        warn(
            "TLS verification is disabled for some outbound calls",
            "Expected behind a corporate proxy; do not ship this way.",
        )


def check_data_files() -> None:
    section("Source data")
    wanted = {
        "locations": ("locations.csv", "location_nodes.csv"),
        "incidents": ("incidents.csv", "incident_nodes.csv"),
        "roads": ("road_connections.csv", "location_relationships.csv"),
        "incident links": ("incident_locations.csv",),
        "alternates": ("alternate_routes.csv",),
    }
    data_dir = ROOT / "data"
    if not data_dir.exists():
        bad("data/ directory not found", "Place the CSV files under data/.")
        return

    for label, names in wanted.items():
        found = None
        for name in names:
            matches = list(data_dir.rglob(name))
            if matches:
                found = matches[0]
                break
        if found:
            ok(f"{label}: {found.relative_to(ROOT)}")
        else:
            bad(f"{label}: none of {', '.join(names)} found under data/")

    fleet = list(data_dir.rglob("missing_data_template.csv"))
    if fleet:
        ok(f"vehicle profiles: {fleet[0].relative_to(ROOT)}")
    else:
        warn(
            "missing_data_template.csv not found",
            "Fleet constraints (capacity, driver hours, SLA) stay unverifiable.",
        )


async def _graph_checks() -> None:
    from app.kg.client import get_kg_client

    client = get_kg_client()
    health = await client.health()
    if not health.get("ok"):
        bad(
            f"Neo4j unreachable: {health.get('reason', 'unknown')}",
            "Start Neo4j and confirm NEO4J_URI / credentials.",
        )
        await client.close()
        return

    ok(f"Neo4j reachable at {health.get('uri')}")

    counts = {
        "Location": "MATCH (n:Location) RETURN count(n) AS n",
        "Incident": "MATCH (n:Incident) RETURN count(n) AS n",
        "VehicleProfile": "MATCH (n:VehicleProfile) RETURN count(n) AS n",
    }
    loaded = {}
    for label, query in counts.items():
        rows = await client.try_run(query)
        loaded[label] = rows[0]["n"] if rows else 0

    if loaded["Location"] == 0:
        bad(
            "No :Location nodes in the graph",
            "Load the data: python scripts/load_graph.py",
        )
    else:
        ok(
            f"Graph loaded — {loaded['Location']} locations, "
            f"{loaded['Incident']} incidents, "
            f"{loaded['VehicleProfile']} vehicle profiles"
        )

    if loaded["Location"] and loaded["VehicleProfile"] == 0:
        warn("No :VehicleProfile nodes — reload to activate fleet constraints")

    # Prove the pathfinder actually works on this data.
    if loaded["Location"]:
        from app.domain.network import load_network

        network = await load_network()
        names = sorted(network.locations)
        if len(names) >= 2:
            found = None
            for origin in names[:6]:
                for destination in reversed(names):
                    if origin == destination:
                        continue
                    paths = network.plan(origin, destination, k=1)
                    if paths:
                        found = (origin, destination, paths[0])
                        break
                if found:
                    break
            if found:
                origin, destination, path = found
                ok(
                    f"Pathfinding works — {origin} to {destination}: "
                    f"{path.total_distance_km:.0f} km over {len(path.stops)} stops"
                )
            else:
                warn("No route could be planned between any sampled pair")

    await client.close()


def check_graph() -> None:
    section("Neo4j")
    try:
        asyncio.run(_graph_checks())
    except Exception as exc:  # noqa: BLE001
        bad(f"Graph check failed: {str(exc)[:200]}")


def check_frontend() -> None:
    section("Frontend")
    node = shutil.which("node")
    npm = shutil.which("npm")

    if node:
        try:
            version = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=20
            ).stdout.strip()
            ok(f"Node {version}")
        except Exception:  # noqa: BLE001
            warn("Node found but did not report a version")
    else:
        bad("Node.js not found", "Install Node 20 or newer to run the UI.")

    if not npm:
        bad("npm not found")

    for name in ("dashboard", "frontend"):
        directory = ROOT / name
        if not directory.exists():
            warn(f"{name}/ not present")
            continue
        if (directory / "node_modules").exists():
            ok(f"{name}/ dependencies installed")
        else:
            warn(
                f"{name}/ dependencies not installed",
                f"Run: npm install --prefix {name}",
            )

    env_local = ROOT / "dashboard" / ".env.local"
    if env_local.exists():
        ok("dashboard/.env.local present (Google Maps browser key)")
    else:
        warn(
            "dashboard/.env.local missing",
            "The map will show 'Map unavailable'. Copy dashboard/.env.example "
            "to dashboard/.env.local and add a browser key.",
        )


def main() -> int:
    print("LogiPilot AI — environment check")
    print("=" * 34)

    check_python()
    check_packages()
    check_env()
    check_data_files()
    check_graph()
    check_frontend()

    section("Summary")
    if failures:
        print(f"  {len(failures)} blocking issue(s):")
        for item in failures:
            print(f"    - {item}")
    if warnings:
        print(f"  {len(warnings)} warning(s) — the platform runs, with reduced function.")
    if not failures and not warnings:
        print("  Everything is ready.")
    elif not failures:
        print("  Ready to run.")

    print("\nNext: python -m uvicorn app.api.main:app --port 8010")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
