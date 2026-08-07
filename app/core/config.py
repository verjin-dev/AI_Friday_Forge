from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Central configuration for the platform.

    Every value is environment driven so the same image runs across enterprise
    environments without code changes.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # Platform
    # ------------------------------------------------------------------
    app_name: str = "LogiPilot AI"
    app_tagline: str = "Constraint-aware logistics intelligence"
    environment: Literal["local", "dev", "staging", "prod"] = "local"
    platform_domain: str = Field(
        default="logistics",
        description="Active enterprise domain. Drives ontology + prompt framing.",
    )
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    # LLM (LangChain OpenAI compatible gateway)
    # ------------------------------------------------------------------
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o"
    openai_verify_ssl: bool = True
    llm_temperature: float = 0.1
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 3
    llm_streaming: bool = True
    llm_max_tokens: int | None = None

    # ------------------------------------------------------------------
    # Neo4j knowledge graph
    # ------------------------------------------------------------------
    neo4j_uri: str = "neo4j://127.0.0.1:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str | None = None
    neo4j_database: str = "neo4j"
    neo4j_max_connection_pool_size: int = 25
    neo4j_connection_timeout: float = 15.0
    neo4j_query_timeout: float = 30.0
    neo4j_max_rows: int = 200
    neo4j_schema_cache_seconds: int = 300

    #: Incident severities that make a location impassable. Only Critical by
    #: default — High and below are modelled as delay, not as a hard block.
    blocking_severities: list[str] = Field(default_factory=lambda: ["critical", "high"])

    # ------------------------------------------------------------------
    # Routing engine
    # ------------------------------------------------------------------
    #: Default enterprise routing algorithm: "astar" (with Yen's for alternatives).
    routing_algorithm: str = "astar"
    #: Node count at or above which A* is preferred over Dijkstra.
    astar_node_threshold: int = 250
    route_candidate_count: int = 4
    #: Algorithm used for mid-journey segment replanning — A* is the right
    #: default because replanning is latency-sensitive.
    replan_algorithm: str = "astar"
    replan_detour_warn_km: float = 25.0

    # --- feature flags ---
    #: When true, /api/routes/plan takes its candidates from the routing engine
    #: (Dijkstra / A* / Yen with enterprise cost) rather than the legacy
    #: enumeration. Off returns the previous behaviour, for A/B comparison.
    routing_engine_drives_plan: bool = True
    enable_incident_overlay: bool = True
    enable_live_traffic_enrichment: bool = True
    enable_segment_replanning: bool = True
    enable_route_monitoring: bool = False

    # ------------------------------------------------------------------
    # Route monitoring
    # ------------------------------------------------------------------
    monitor_poll_interval_seconds: int = 120
    monitor_replan_threshold_minutes: float = 15.0
    monitor_max_active_routes: int = 50
    monitor_weather_check: bool = True

    # ------------------------------------------------------------------
    # Overlay persistence
    # ------------------------------------------------------------------
    overlay_persist_to_graph: bool = False
    overlay_default_ttl_hours: float = 24.0

    # --- cost model weights (all default to 1.0 = neutral) ---
    weight_distance: float = 1.0
    weight_travel_time: float = 1.0
    weight_road_quality: float = 1.0
    weight_historical_delay: float = 1.0
    weight_risk: float = 1.0
    weight_money: float = 1.0
    weight_priority: float = 1.0
    weight_criticality: float = 1.0

    # --- cost model constants ---
    #: Effective minutes added per km, beyond pure travel time: covers wear,
    #: driver hours and distance-proportional cost.
    minutes_per_km_distance_weight: float = 0.15
    #: Minutes of expected loss at incident_probability = 1.0.
    incident_risk_minutes: float = 45.0
    weather_risk_minutes: float = 20.0
    #: How strongly to prefer strategic corridors over local roads.
    priority_penalty_factor: float = 0.2
    #: Fleet time valuation, used to convert money into effective minutes.
    cost_per_minute: float = 8.0
    fuel_cost_per_km: float = 7.5

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    search_api_provider: Literal["duckduckgo", "none"] = "duckduckgo"
    search_api_base_url: str | None = None
    search_api_key: str | None = None
    search_result_limit: int = 5
    search_verify_ssl: bool = True

    # ------------------------------------------------------------------
    # Workflow behaviour
    # ------------------------------------------------------------------
    workflow_max_reflection_loops: int = 2
    workflow_confidence_threshold: float = 0.6
    workflow_node_timeout_seconds: float = 90.0
    workflow_enable_optimization: bool = True
    workflow_enable_self_improvement: bool = True
    workflow_human_in_the_loop: bool = False

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    security_block_on_injection: bool = True
    security_redact_pii: bool = True
    security_default_role: str = "analyst"
    security_audit_log_path: Path = PROJECT_ROOT / "data" / "security_audit.jsonl"

    # ------------------------------------------------------------------
    # MCP
    # ------------------------------------------------------------------
    mcp_enabled: bool = True
    mcp_config_path: Path = PROJECT_ROOT / "mcp_servers.json"
    mcp_filesystem_root: Path = PROJECT_ROOT / "data" / "documents"
    mcp_tool_timeout_seconds: float = 45.0

    # Optional relational source for the sql_query tool (read-only SQLite).
    sqlite_path: Path | None = None

    # Hosts the rest_get tool may reach. Empty list disables outbound REST.
    rest_allowlist: list[str] = Field(default_factory=list)

    # Keyless public services used by the logistics tools.
    weather_api_url: str = "https://api.open-meteo.com/v1/forecast"
    geocoding_api_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    routing_api_url: str = "https://router.project-osrm.org/route/v1/driving"
    external_api_timeout_seconds: float = 20.0
    #: Corporate proxies re-sign TLS for outbound traffic, which breaks
    #: certificate validation for public APIs. Set false on such networks.
    external_verify_ssl: bool = True

    # Google Maps Platform (optional — tools stay unregistered without a key).
    google_maps_api_key: str | None = None
    google_roads_snap_url: str = "https://roads.googleapis.com/v1/snapToRoads"
    google_directions_url: str = (
        "https://maps.googleapis.com/maps/api/directions/json"
    )
    #: Routes API v2 — the live traffic source. Returns both traffic-aware
    #: `duration` and free-flow `staticDuration`; their difference is the
    #: measured congestion delay.
    google_routes_url: str = (
        "https://routes.googleapis.com/directions/v2:computeRoutes"
    )
    #: Fall back to the heuristic delay model when the live call fails.
    live_traffic_enabled: bool = True

    @property
    def google_maps_enabled(self) -> bool:
        return bool(self.google_maps_api_key)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "enterprise-ai-platform"
    metrics_store_path: Path = PROJECT_ROOT / "data" / "runs.jsonl"
    learning_store_path: Path = PROJECT_ROOT / "data" / "learning.jsonl"

    # Approximate USD per 1M tokens; override per gateway contract.
    cost_per_million_input_tokens: float = 2.50
    cost_per_million_output_tokens: float = 10.00

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8030
    api_cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    @field_validator("api_cors_origins", "rest_allowlist", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("search_api_provider", mode="before")
    @classmethod
    def _normalise_provider(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return "none"
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @property
    def llm_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def neo4j_configured(self) -> bool:
        return bool(self.neo4j_password)

    def ensure_data_dirs(self) -> None:
        for path in (
            self.security_audit_log_path,
            self.metrics_store_path,
            self.learning_store_path,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.mcp_filesystem_root.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
