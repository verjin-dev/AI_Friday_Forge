import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { CheckCircle2, ShieldAlert, TriangleAlert, Zap } from "lucide-react";

import FleetMap from "../components/FleetMap.jsx";
import PageState from "../components/PageState.jsx";
import { fetchLocations, fetchProfiles, planRoute } from "../data/fleet.js";

function Verdict({ check }) {
  const failed = !check.satisfied;
  const hard = check.severity === "hard";
  const colour = !failed
    ? "var(--emerald)"
    : hard
    ? "var(--rose)"
    : "var(--amber)";
  const label = !failed ? "PASS" : hard ? "FAIL" : "WARN";

  return (
    <div className="verdict-row">
      <span className="verdict-flag" style={{ color: colour }}>
        {label}
      </span>
      <span className="verdict-code">{check.code}</span>
      <span className="verdict-detail">{check.detail}</span>
    </div>
  );
}

export default function RoutesPage({ notify }) {
  const [locations, setLocations] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [profile, setProfile] = useState("");
  const [plan, setPlan] = useState(null);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetchLocations(controller.signal),
      fetchProfiles(controller.signal),
    ])
      .then(([names, profilePayload]) => {
        setLocations(names);
        setProfiles(profilePayload.profiles || []);
        if (names.length) {
          setOrigin(names[0]);
          setDestination(names[names.length - 1]);
        }
      })
      .catch((exc) => {
        if (exc.name !== "AbortError") setError(exc.message);
      });
    return () => controller.abort();
  }, []);

  const run = useCallback(async () => {
    if (!origin || !destination || origin === destination) {
      notify("Choose two different locations.", "error");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const payload = await planRoute({ origin, destination, profile });
      setPlan(payload);
      const recommended =
        payload.routes.find((r) => r.label === payload.recommended_label) ||
        payload.routes[0];
      setSelected(recommended || null);
      notify(
        payload.all_infeasible
          ? "No compliant route exists under the current incident state."
          : `${payload.feasible_count} of ${payload.route_count} routes are compliant.`,
        payload.all_infeasible ? "error" : "success"
      );
    } catch (exc) {
      setError(exc.message);
      setPlan(null);
    } finally {
      setLoading(false);
    }
  }, [origin, destination, profile, notify]);

  // Shape the chosen route so FleetMap can draw it.
  const mapTruck = selected
    ? {
        id: plan?.recommended_label || "route",
        route: `${plan.origin} to ${plan.destination}`,
        stops: (selected.coordinates || []).map((point, index) => ({
          name: selected.stops[index],
          lat: point.latitude,
          lng: point.longitude,
        })),
      }
    : null;

  return (
    <>
      <section className="card">
        <div className="panel-head">
          <h3>Route planner</h3>
          <span className="sub">graph pathfinding · hard constraints · delay</span>
        </div>

        <div className="planner">
          <label className="field">
            <span className="sr-only">Origin</span>
            <select
              value={origin}
              onChange={(event) => setOrigin(event.target.value)}
              style={{ flex: 1, background: "none", border: "none", outline: "none" }}
            >
              {locations.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span className="sr-only">Destination</span>
            <select
              value={destination}
              onChange={(event) => setDestination(event.target.value)}
              style={{ flex: 1, background: "none", border: "none", outline: "none" }}
            >
              {locations.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span className="sr-only">Vehicle profile</span>
            <select
              value={profile}
              onChange={(event) => setProfile(event.target.value)}
              style={{ flex: 1, background: "none", border: "none", outline: "none" }}
            >
              <option value="">No vehicle (network rules only)</option>
              {profiles.map((item) => (
                <option key={item.profile_id} value={item.profile_id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          <button
            type="button"
            className="pill-btn primary"
            onClick={run}
            disabled={loading}
          >
            <Zap size={14} aria-hidden="true" />
            {loading ? "Planning…" : "Plan"}
          </button>
        </div>

        <PageState
          loading={loading}
          error={error}
          empty={!plan}
          emptyText="Choose an origin and destination, then plan."
        >
          {plan && (
            <div style={{ padding: "12px 16px 4px" }}>
              <div className="summary-row">
                <span
                  className="status-tag"
                  style={{
                    color: plan.all_infeasible ? "var(--rose)" : "var(--emerald)",
                  }}
                >
                  {plan.all_infeasible ? (
                    <ShieldAlert size={12} aria-hidden="true" />
                  ) : (
                    <CheckCircle2 size={12} aria-hidden="true" />
                  )}
                  {plan.feasible_count} / {plan.route_count} compliant
                </span>
                {plan.vehicle && (
                  <span className="status-tag">{plan.vehicle}</span>
                )}
                {plan.weather?.found && (
                  <span className="status-tag">
                    weather at {plan.weather.location}
                  </span>
                )}
              </div>

              {plan.alerts?.map((alert, index) => (
                <p
                  key={index}
                  className="violation"
                  style={{
                    color:
                      alert.level === "critical" ? "var(--rose)" : "var(--amber)",
                  }}
                >
                  <TriangleAlert size={12} aria-hidden="true" /> {alert.message}
                </p>
              ))}
            </div>
          )}
        </PageState>
      </section>

      {plan && (
        <section className="split">
          <div className="card map-card">
            <div className="panel-head">
              <h3>Selected route</h3>
              <span className="sub">{selected?.label || "—"}</span>
            </div>
            <FleetMap
              trucks={[]}
              selectedTruck={mapTruck}
              routeRequest={null}
              onError={(message) => notify(message, "error")}
            />
          </div>

          <section className="card alerts">
            <div className="panel-head">
              <h3>Options</h3>
              <span className="sub">{plan.route_count} found</span>
            </div>
            <div className="alert-list">
              {plan.routes.map((route) => (
                <motion.button
                  key={route.label}
                  type="button"
                  className="alert"
                  onClick={() => setSelected(route)}
                  initial={{ opacity: 0, x: 8 }}
                  animate={{ opacity: 1, x: 0 }}
                  style={{
                    borderColor:
                      selected?.label === route.label
                        ? "var(--cyan)"
                        : "var(--border)",
                    borderLeft: `3px solid ${
                      route.feasible ? "var(--emerald)" : "var(--rose)"
                    }`,
                  }}
                >
                  <span style={{ minWidth: 0, flex: 1 }}>
                    <span className="alert-title">{route.label}</span>
                    <span className="alert-detail">
                      {route.total_distance_km} km ·{" "}
                      {route.delay.predicted_total_minutes} min ETA ·{" "}
                      {route.delay.risk} risk
                    </span>
                    <span className="alert-meta">
                      <span
                        style={{
                          color: route.feasible
                            ? "var(--emerald)"
                            : "var(--rose)",
                        }}
                      >
                        {route.feasible ? "compliant" : "disqualified"}
                      </span>
                      {plan.recommended_label === route.label && (
                        <span style={{ color: "var(--cyan)" }}>· recommended</span>
                      )}
                      {route.delay.live_traffic_used && <span>· live traffic</span>}
                    </span>
                  </span>
                </motion.button>
              ))}
            </div>
          </section>
        </section>
      )}

      {selected && (
        <section className="card">
          <div className="panel-head">
            <h3>Constraint report</h3>
            <span className="sub">{selected.label}</span>
          </div>

          <div className="verdicts">
            {selected.constraint_report.checks.map((check) => (
              <Verdict key={check.code} check={check} />
            ))}
          </div>

          {selected.delay.factors.length > 0 && (
            <div className="drawer" style={{ borderBottom: "none" }}>
              <h5>Delay factors — {selected.delay.predicted_delay_minutes} min total</h5>
              <ul
                style={{
                  margin: 0,
                  paddingLeft: 18,
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  lineHeight: 1.7,
                }}
              >
                {selected.delay.factors.map((factor, index) => (
                  <li key={index}>
                    <strong style={{ color: "var(--text)" }}>
                      +{factor.minutes} min
                    </strong>{" "}
                    {factor.name} — {factor.evidence}
                  </li>
                ))}
              </ul>
              {selected.delay.notes?.map((note, index) => (
                <p
                  key={index}
                  style={{
                    fontSize: 11.5,
                    color: "var(--text-faint)",
                    margin: "6px 0 0",
                  }}
                >
                  {note}
                </p>
              ))}
            </div>
          )}

          {selected.constraint_report.unverifiable.length > 0 && (
            <p className="provenance">
              Not verifiable from available data:{" "}
              {selected.constraint_report.unverifiable.join(", ")}
            </p>
          )}
        </section>
      )}
    </>
  );
}
