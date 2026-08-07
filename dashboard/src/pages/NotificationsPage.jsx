import { useCallback, useEffect, useState } from "react";
import {
  CheckSquare,
  Power,
  ShieldCheck,
  Square,
  Zap,
  ZapOff,
} from "lucide-react";

import PageState from "../components/PageState.jsx";
import {
  fetchNetwork,
  fetchSecurityAudit,
  setIncidentStatus,
} from "../data/fleet.js";

const SEVERITY_TONE = {
  Critical: "var(--rose)",
  High: "#ff8c42",
  Medium: "var(--amber)",
  Low: "var(--text-faint)",
};

export default function NotificationsPage({ search, notify, onFleetChanged }) {
  const [network, setNetwork] = useState(null);
  const [audit, setAudit] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [selectedIds, setSelectedIds] = useState(new Set());

  const load = useCallback(async (signal) => {
    try {
      const [networkPayload, auditPayload] = await Promise.all([
        fetchNetwork(signal),
        fetchSecurityAudit(signal).catch(() => []),
      ]);
      setNetwork(networkPayload);
      setAudit(auditPayload);
    } catch (exc) {
      if (exc.name !== "AbortError") setError(exc.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const toggle = async (incident) => {
    const next = incident.is_active ? "Inactive" : "Active";
    setBusy(incident.incident_id);
    try {
      const payload = await setIncidentStatus(incident.incident_id, next);
      setNetwork(payload.network);
      notify(`${incident.incident_id} set to ${next}. Lanes re-planned.`, "success");
      onFleetChanged?.();
    } catch (exc) {
      notify(exc.message, "error");
    } finally {
      setBusy(null);
    }
  };

  const handleBulkToggle = async (targetIncidents, targetStatus) => {
    if (!targetIncidents || targetIncidents.length === 0) return;
    setBusy("bulk");
    try {
      let lastPayload = null;
      for (const inc of targetIncidents) {
        lastPayload = await setIncidentStatus(inc.incident_id, targetStatus);
      }
      if (lastPayload) setNetwork(lastPayload.network);
      setSelectedIds(new Set());
      notify(
        `Bulk updated ${targetIncidents.length} incident(s) to ${targetStatus}. Routes re-planned.`,
        "success"
      );
      onFleetChanged?.();
    } catch (exc) {
      notify(`Bulk update error: ${exc.message}`, "error");
    } finally {
      setBusy(null);
    }
  };

  const term = search.trim().toLowerCase();
  const incidents = (network?.incidents || []).filter((incident) =>
    !term
      ? true
      : [incident.incident_id, incident.type, incident.location, incident.severity]
          .some((value) => String(value).toLowerCase().includes(term))
  );

  const active = incidents.filter((incident) => incident.is_active);
  const inactive = incidents.filter((incident) => !incident.is_active);

  const allSelected = incidents.length > 0 && selectedIds.size === incidents.length;

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(incidents.map((i) => i.incident_id)));
    }
  };

  const toggleSelectOne = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectedIncidents = incidents.filter((i) => selectedIds.has(i.incident_id));

  return (
    <PageState loading={loading} error={error}>
      <section className="card">
        <div className="panel-head" style={{ flexWrap: "wrap", gap: "10px" }}>
          <h3>Incidents</h3>
          <span className="sub">
            {active.length} active of {incidents.length}
          </span>
          <span className="sub" style={{ marginRight: "auto" }}>
            {network?.blocked_locations?.length || 0} locations blocked
          </span>

          {/* Bulk Action Controls */}
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            {selectedIds.size > 0 ? (
              <>
                <button
                  type="button"
                  className="pill-btn"
                  onClick={() => handleBulkToggle(selectedIncidents, "Inactive")}
                  disabled={busy === "bulk"}
                  style={{ color: "var(--emerald)", borderColor: "var(--emerald)" }}
                >
                  <ZapOff size={13} />
                  Clear Selected ({selectedIds.size})
                </button>
                <button
                  type="button"
                  className="pill-btn"
                  onClick={() => handleBulkToggle(selectedIncidents, "Active")}
                  disabled={busy === "bulk"}
                  style={{ color: "var(--rose)", borderColor: "var(--rose)" }}
                >
                  <Zap size={13} />
                  Activate Selected ({selectedIds.size})
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="pill-btn"
                  onClick={() => handleBulkToggle(active, "Inactive")}
                  disabled={busy === "bulk" || active.length === 0}
                  title="Clear all active incidents in bulk"
                >
                  <ZapOff size={13} />
                  Clear All Active ({active.length})
                </button>
                <button
                  type="button"
                  className="pill-btn"
                  onClick={() => handleBulkToggle(inactive, "Active")}
                  disabled={busy === "bulk" || inactive.length === 0}
                  title="Activate all inactive incidents in bulk"
                >
                  <Zap size={13} />
                  Activate All ({inactive.length})
                </button>
              </>
            )}
          </div>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: "40px", textAlign: "center" }}>
                  <button
                    type="button"
                    onClick={toggleSelectAll}
                    style={{ background: "none", border: "none", cursor: "pointer", color: "var(--cyan)" }}
                    title={allSelected ? "Deselect all" : "Select all"}
                  >
                    {allSelected ? <CheckSquare size={15} /> : <Square size={15} />}
                  </button>
                </th>
                <th>Incident</th>
                <th>Type</th>
                <th>Severity</th>
                <th>Location</th>
                <th>Status</th>
                <th style={{ textAlign: "right" }}>Scenario</th>
              </tr>
            </thead>
            <tbody>
              {incidents.length === 0 && (
                <tr>
                  <td colSpan={7}>
                    <p className="empty-state">No incidents match your search.</p>
                  </td>
                </tr>
              )}
              {incidents.map((incident, index) => {
                const isSelected = selectedIds.has(incident.incident_id);
                return (
                  <tr
                    key={`${incident.incident_id}-${incident.location}-${index}`}
                    style={{ background: isSelected ? "rgba(86, 216, 238, 0.06)" : undefined }}
                  >
                    <td style={{ textAlign: "center" }}>
                      <button
                        type="button"
                        onClick={() => toggleSelectOne(incident.incident_id)}
                        style={{ background: "none", border: "none", cursor: "pointer", color: isSelected ? "var(--cyan)" : "var(--text-faint)" }}
                      >
                        {isSelected ? <CheckSquare size={15} /> : <Square size={15} />}
                      </button>
                    </td>
                    <td className="cell-primary">{incident.incident_id}</td>
                    <td>{incident.type}</td>
                    <td>
                      <span
                        className="status-tag"
                        style={{ color: SEVERITY_TONE[incident.severity] }}
                      >
                        <i
                          className="legend-swatch"
                          style={{ background: SEVERITY_TONE[incident.severity] }}
                          aria-hidden="true"
                        />
                        {incident.severity}
                      </span>
                      {incident.is_blocking && (
                        <div className="cell-sub" style={{ color: "var(--rose)" }}>
                          blocks routing
                        </div>
                      )}
                    </td>
                    <td>{incident.location}</td>
                    <td>
                      <span
                        className="status-tag"
                        style={{
                          color: incident.is_active
                            ? "var(--emerald)"
                            : "var(--text-faint)",
                        }}
                      >
                        {incident.status}
                      </span>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        type="button"
                        className="pill-btn"
                        onClick={() => toggle(incident)}
                        disabled={busy === incident.incident_id || busy === "bulk"}
                        title={
                          incident.is_active
                            ? `Clear ${incident.incident_id} and re-plan`
                            : `Activate ${incident.incident_id} and re-plan`
                        }
                      >
                        <Power size={12} aria-hidden="true" />
                        {incident.is_active ? "Clear" : "Activate"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <div className="panel-head">
          <ShieldCheck size={15} aria-hidden="true" style={{ color: "var(--emerald)" }} />
          <h3>Security audit</h3>
          <span className="sub">{audit.length} events</span>
        </div>
        {audit.length === 0 ? (
          <p className="empty-state">
            No security events recorded. Events appear here when the agent
            workflow runs.
          </p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                  <th>Role</th>
                  <th>Verdict</th>
                  <th>Findings</th>
                </tr>
              </thead>
              <tbody>
                {audit.slice(0, 20).map((event, index) => (
                  <tr key={index}>
                    <td className="cell-sub">
                      {String(event.ts || "").slice(11, 19)}
                    </td>
                    <td>{event.event}</td>
                    <td>{event.role}</td>
                    <td
                      style={{
                        color: event.allowed ? "var(--emerald)" : "var(--rose)",
                      }}
                    >
                      {event.allowed ? "allowed" : "blocked"}
                    </td>
                    <td className="cell-sub">
                      {(event.findings || []).length} · max{" "}
                      {event.max_severity || "info"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </PageState>
  );
}
