/**
 * Deterministic route planner controls plus the incident scenario switches
 * that drive the dynamic re-planning demonstration.
 */
export default function Planner({
  locations,
  origin,
  destination,
  onOriginChange,
  onDestinationChange,
  onPlan,
  planning,
  incidents,
  onToggleIncident,
  busyIncident,
}) {
  return (
    <div
      style={{
        padding: "10px 14px",
        borderBottom: "1px solid var(--line)",
        display: "flex",
        gap: 8,
        alignItems: "center",
        flexWrap: "wrap",
      }}
    >
      <select value={origin} onChange={(e) => onOriginChange(e.target.value)}>
        <option value="">from…</option>
        {locations.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>

      <span style={{ color: "var(--muted)" }}>→</span>

      <select
        value={destination}
        onChange={(e) => onDestinationChange(e.target.value)}
      >
        <option value="">to…</option>
        {locations.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>

      <button
        className="send"
        style={{ padding: "5px 16px" }}
        onClick={onPlan}
        disabled={!origin || !destination || origin === destination || planning}
      >
        {planning ? "Planning…" : "Plan route"}
      </button>

      {incidents.length > 0 && (
        <div
          style={{
            display: "flex",
            gap: 6,
            alignItems: "center",
            marginLeft: "auto",
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            scenario:
          </span>
          {incidents.map((incident) => (
            <button
              key={incident.incident_id}
              onClick={() =>
                onToggleIncident(
                  incident.incident_id,
                  incident.is_active ? "Inactive" : "Active"
                )
              }
              disabled={busyIncident === incident.incident_id}
              className="badge"
              style={{
                border: "1px solid var(--line)",
                background: incident.is_active ? "#2c1416" : "var(--chip)",
                color: incident.is_active ? "var(--bad)" : "var(--muted)",
                cursor: "pointer",
              }}
              title={`${incident.severity} ${incident.type} at ${incident.location} — click to ${
                incident.is_active ? "clear" : "activate"
              }`}
            >
              {incident.incident_id} {incident.is_active ? "on" : "off"}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
