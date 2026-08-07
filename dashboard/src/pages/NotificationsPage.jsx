import { useCallback, useEffect, useState } from "react";
import { Power, ShieldCheck } from "lucide-react";

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

  const term = search.trim().toLowerCase();
  const incidents = (network?.incidents || []).filter((incident) =>
    !term
      ? true
      : [incident.incident_id, incident.type, incident.location, incident.severity]
          .some((value) => String(value).toLowerCase().includes(term))
  );

  const active = incidents.filter((incident) => incident.is_active);

  return (
    <PageState loading={loading} error={error}>
      <section className="card">
        <div className="panel-head">
          <h3>Incidents</h3>
          <span className="sub">
            {active.length} active of {incidents.length}
          </span>
          <span className="sub" style={{ marginLeft: "auto" }}>
            {network?.blocked_locations?.length || 0} locations blocked
          </span>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
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
                  <td colSpan={6}>
                    <p className="empty-state">No incidents match your search.</p>
                  </td>
                </tr>
              )}
              {incidents.map((incident) => (
                <tr key={incident.incident_id}>
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
                      disabled={busy === incident.incident_id}
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
              ))}
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
