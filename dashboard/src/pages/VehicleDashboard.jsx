import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Gauge,
  LayoutDashboard,
  MessageSquare,
  Phone,
  Route as RouteIcon,
  TriangleAlert,
} from "lucide-react";

import DriverRail from "../components/driver/DriverRail.jsx";
import FleetMap from "../components/FleetMap.jsx";
import NavigationOverlay from "../components/vehicle/NavigationOverlay.jsx";
import Toast from "../components/Toast.jsx";
import TripTimeline from "../components/vehicle/TripTimeline.jsx";
import { ROLES } from "../config/demoAuth.js";
import { fetchFleet } from "../data/fleet.js";

const TOAST_MS = 3600;

function KV({ k, v, muted }) {
  return (
    <div className="kv-item">
      <span className="k">{k}</span>
      <span className={`v ${muted ? "muted" : ""}`}>{v}</span>
    </div>
  );
}

export default function VehicleDashboard({ session, onSignOut, onNavigate }) {
  const [fleet, setFleet] = useState({ trucks: [], alerts: [] });
  const [activeId, setActiveId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState(null);
  const timer = useRef(null);

  const notify = useCallback((message, kind = "info") => {
    setToast({ message, kind, id: Date.now() });
  }, []);

  useEffect(() => {
    if (!toast) return undefined;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(timer.current);
  }, [toast]);

  useEffect(() => {
    let cancelled = false;
    fetchFleet()
      .then((payload) => {
        if (cancelled) return;
        setFleet(payload);
        const moving =
          payload.trucks.find((truck) => truck.status !== "At depot") ||
          payload.trucks[0];
        setActiveId(moving?.id || null);
      })
      .catch((exc) => notify(`Could not load the trip: ${exc.message}`, "error"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [notify]);

  const truck = useMemo(
    () => fleet.trucks.find((item) => item.id === activeId) || null,
    [fleet.trucks, activeId]
  );

  const alerts = useMemo(
    () => fleet.alerts.filter((alert) => alert.truck === activeId),
    [fleet.alerts, activeId]
  );

  return (
    <div className="vehicle">
      <DriverRail
        session={session}
        truck={truck}
        trucks={fleet.trucks}
        onSelect={setActiveId}
        onSignOut={onSignOut}
      />

      <main className="vehicle-main">
        <section className="card nav-card">
          <NavigationOverlay truck={truck} />
          {loading ? (
            <p className="empty-state">Loading the current trip…</p>
          ) : (
            <FleetMap
              trucks={truck ? [truck] : []}
              selectedTruck={truck}
              routeRequest={null}
              onError={(message) => notify(message, "error")}
            />
          )}
        </section>

        {alerts.length > 0 && (
          <section className="card">
            <div className="panel-head">
              <TriangleAlert size={15} aria-hidden="true" style={{ color: "var(--amber)" }} />
              <h3>Road conditions</h3>
              <span className="sub">{alerts.length} on this route</span>
            </div>
            {alerts.map((alert) => (
              <div
                key={alert.id}
                className={`route-alert ${alert.severity === "critical" ? "critical" : ""}`}
              >
                <AlertTriangle
                  size={15}
                  aria-hidden="true"
                  style={{
                    flex: "none",
                    marginTop: 2,
                    color:
                      alert.severity === "critical" ? "var(--rose)" : "var(--amber)",
                  }}
                />
                <span>
                  <strong style={{ display: "block", fontSize: 12.5 }}>
                    {alert.title}
                  </strong>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    {alert.detail}
                  </span>
                </span>
              </div>
            ))}
          </section>
        )}

        <section className="card">
          <div className="panel-head">
            <RouteIcon size={15} aria-hidden="true" style={{ color: "var(--cyan)" }} />
            <h3>Trip timeline</h3>
            <span className="sub">{truck?.timeline?.length || 0} stops</span>
          </div>
          <TripTimeline timeline={truck?.timeline} />
        </section>

        <div className="vehicle-grid">
          <section className="card">
            <div className="panel-head">
              <h3>Delivery</h3>
            </div>
            <div className="kv-list">
              <KV k="Route" v={truck?.route || "—"} />
              <KV k="Next stop" v={truck?.next_stop || "—"} />
              <KV k="Distance left" v={`${truck?.distance_remaining_km ?? 0} km`} />
              <KV k="Departure" v={truck?.departure || "—"} />
              <KV k="Planned arrival" v={truck?.eta || "—"} />
            </div>
          </section>

          <section className="card">
            <div className="panel-head">
              <h3>Cargo</h3>
            </div>
            <div className="kv-list">
              <KV k="Shipment" v={truck?.load || "—"} />
              <KV
                k="Vehicle capacity"
                v={
                  truck?.cargo?.capacity_kg
                    ? `${truck.cargo.capacity_kg} kg · ${truck.cargo.capacity_m3} m³`
                    : "—"
                }
              />
              <KV
                k="Refrigerated"
                v={truck?.cargo?.refrigerated ? "yes" : "no"}
              />
              <KV
                k="Hazmat certified"
                v={truck?.cargo?.hazmat_certified ? "yes" : "no"}
              />
              <KV k="Loaded weight" v="not tracked" muted />
              <KV k="Cargo temperature" v="not tracked" muted />
            </div>
          </section>

          <section className="card">
            <div className="panel-head">
              <Gauge size={15} aria-hidden="true" style={{ color: "var(--indigo)" }} />
              <h3>Arrival forecast</h3>
            </div>
            <div className="kv-list">
              <KV
                k="On-time probability"
                v={
                  truck?.predictive
                    ? `${(truck.predictive.on_time_probability * 100).toFixed(0)}%`
                    : "—"
                }
              />
              <KV
                k="Delay probability"
                v={
                  truck?.predictive
                    ? `${(truck.predictive.delay_probability * 100).toFixed(0)}%`
                    : "—"
                }
              />
              <KV
                k="Model confidence"
                v={
                  truck?.predictive
                    ? `${(truck.predictive.confidence * 100).toFixed(0)}%`
                    : "—"
                }
              />
              <KV k="Baseline" v={truck?.predictive?.basis || "—"} />
            </div>
            {truck?.predictive?.note && (
              <p className="provenance">{truck.predictive.note}</p>
            )}
          </section>

          <section className="card">
            <div className="panel-head">
              <h3>Vehicle health</h3>
            </div>
            <div className="kv-list">
              <KV k="Assigned profile" v={truck?.vehicle || "—"} />
              {(truck?.untracked || []).slice(0, 5).map((field) => (
                <KV
                  key={field}
                  k={field.replace(/_/g, " ")}
                  v="not tracked"
                  muted
                />
              ))}
            </div>
            <p className="provenance">
              These come from vehicle telemetry, which this dataset does not
              include. Connect a telematics feed to populate them.
            </p>
          </section>
        </div>

        <section className="card">
          <div className="panel-head">
            <h3>Quick actions</h3>
          </div>
          <div className="driver-actions">
            <button
              type="button"
              className="pill-btn"
              onClick={() => notify("Calling dispatch…", "info")}
            >
              <Phone size={14} aria-hidden="true" />
              Call dispatch
            </button>
            <button
              type="button"
              className="pill-btn"
              onClick={() => notify("Message sent to dispatch.", "success")}
            >
              <MessageSquare size={14} aria-hidden="true" />
              Message
            </button>
            <button
              type="button"
              className="pill-btn"
              onClick={() =>
                notify(
                  "Re-route requested — dispatch will confirm from the operations desk.",
                  "info"
                )
              }
            >
              <RouteIcon size={14} aria-hidden="true" />
              Request re-route
            </button>
            {session?.role === ROLES.ADMIN && (
              <button
                type="button"
                className="pill-btn"
                onClick={() => onNavigate("/")}
              >
                <LayoutDashboard size={14} aria-hidden="true" />
                Operations
              </button>
            )}
          </div>
        </section>
      </main>

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}
