import { motion } from "framer-motion";
import {
  ArrowLeft,
  MessageSquare,
  Package,
  Phone,
  Route as RouteIcon,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";

import TripTimeline from "./vehicle/TripTimeline.jsx";

function Row({ k, v, muted, tone }) {
  return (
    <div className="kv-item">
      <span className="k">{k}</span>
      <span className={`v ${muted ? "muted" : ""}`} style={tone ? { color: tone } : undefined}>
        {v}
      </span>
    </div>
  );
}

export default function TruckDetailsDrawer({ truck, onClose, onAction, onReplan }) {
  if (!truck) return null;

  const delayed = truck.status === "Delayed" || truck.delayRisk === "severe";

  return (
    <motion.aside
      className="truck-drawer"
      role="dialog"
      aria-label={`${truck.id} details`}
      initial={{ opacity: 0, x: 28 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.24, ease: "easeOut" }}
    >
      <header className="drawer-head">
        <button
          type="button"
          className="icon-btn"
          onClick={onClose}
          title="Back to fleet"
          aria-label="Back to fleet"
        >
          <ArrowLeft size={15} />
        </button>
        <div style={{ minWidth: 0, flex: 1 }}>
          <h3>{truck.id}</h3>
          <span className="sub">{truck.route}</span>
        </div>
        <span
          className="status-tag"
          style={{
            color: delayed ? "var(--amber)" : truck.feasible ? "var(--emerald)" : "var(--rose)",
          }}
        >
          {truck.status}
        </span>
        <button
          type="button"
          className="icon-btn drawer-close"
          onClick={onClose}
          title="Close"
          aria-label="Close details"
        >
          <X size={15} />
        </button>
      </header>

      <div className="drawer-body">
        {delayed && (
          <div className="ai-recommendation">
            <Sparkles size={15} aria-hidden="true" style={{ color: "var(--amber)", flex: "none" }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <strong style={{ fontSize: 12.5, display: "block" }}>
                Recommendation
              </strong>
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                {truck.softViolations?.[0] ||
                  truck.hardViolations?.[0] ||
                  `Predicted ${Math.round(truck.delayMinutes || 0)} min delay on this lane.`}
              </span>
            </div>
            <button
              type="button"
              className="pill-btn"
              onClick={() =>
                onAction(`Recommendation acknowledged for ${truck.id}.`, "success")
              }
            >
              Approve
            </button>
          </div>
        )}

        <section className="drawer-section">
          <h4>Trip overview</h4>
          <div className="kv-list">
            <Row k="Driver" v={truck.driver} />
            <Row k="ETA" v={truck.eta} />
            <Row k="Departure" v={truck.departure || "—"} />
            <Row k="Progress" v={`${truck.progress}%`} />
            <Row k="Next stop" v={truck.next_stop || "—"} />
            <Row
              k="Distance remaining"
              v={
                truck.distance_remaining_km != null
                  ? `${truck.distance_remaining_km} km of ${truck.distanceKm} km`
                  : "—"
              }
            />
            <Row k="Shipment" v={truck.load} />
          </div>
        </section>

        <section className="drawer-section">
          <h4>Trip timeline</h4>
          <TripTimeline timeline={truck.timeline} />
        </section>

        <section className="drawer-section">
          <h4>Predictive intelligence</h4>
          <div className="kv-list">
            <Row
              k="On-time probability"
              v={
                truck.predictive
                  ? `${(truck.predictive.on_time_probability * 100).toFixed(0)}%`
                  : "—"
              }
              tone={
                truck.predictive?.on_time_probability > 0.6
                  ? "var(--emerald)"
                  : "var(--amber)"
              }
            />
            <Row
              k="Delay probability"
              v={
                truck.predictive
                  ? `${(truck.predictive.delay_probability * 100).toFixed(0)}%`
                  : "—"
              }
            />
            <Row
              k="Confidence"
              v={
                truck.predictive
                  ? `${(truck.predictive.confidence * 100).toFixed(0)}%`
                  : "—"
              }
            />
            <Row k="Baseline" v={truck.predictive?.basis || "—"} />
          </div>
          {truck.delayFactors?.length > 0 && (
            <ul className="factor-list">
              {truck.delayFactors.map((factor, index) => (
                <li key={index}>{factor}</li>
              ))}
            </ul>
          )}
        </section>

        <section className="drawer-section">
          <h4>Vehicle and cargo</h4>
          <div className="kv-list">
            <Row k="Profile" v={truck.vehicle || "unassigned"} />
            <Row
              k="Capacity"
              v={
                truck.cargo?.capacity_kg
                  ? `${truck.cargo.capacity_kg} kg · ${truck.cargo.capacity_m3} m³`
                  : "—"
              }
            />
            <Row k="Refrigerated" v={truck.cargo?.refrigerated ? "yes" : "no"} />
            <Row k="Hazmat" v={truck.cargo?.hazmat_certified ? "certified" : "no"} />
            <Row k="Loaded weight" v="not tracked" muted />
          </div>
        </section>

        <section className="drawer-section">
          <h4>Constraint verdict</h4>
          {truck.hardViolations?.map((item, index) => (
            <p className="violation" key={`h-${index}`}>
              <TriangleAlert size={12} aria-hidden="true" /> {item}
            </p>
          ))}
          {truck.softViolations?.map((item, index) => (
            <p
              className="violation"
              style={{ color: "var(--amber)" }}
              key={`s-${index}`}
            >
              <TriangleAlert size={12} aria-hidden="true" /> {item}
            </p>
          ))}

          {truck.replanOutcome && (
            <div
              style={{
                marginTop: 10,
                padding: "10px 12px",
                background: "rgba(255, 140, 66, 0.08)",
                borderRadius: 8,
                border: "1px solid rgba(255, 140, 66, 0.25)",
                fontSize: 12,
              }}
            >
              <div
                style={{
                  fontWeight: 600,
                  color: "var(--amber)",
                  marginBottom: 6,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <RouteIcon size={13} /> Replanning Breakdown
              </div>
              <div className="kv-list" style={{ gap: 5 }}>
                <Row
                  k="Replanned From"
                  v={truck.replanOutcome.replanned_from || truck.next_stop || "Current position"}
                />
                <Row
                  k="Reason"
                  v={truck.replanOutcome.reason || "Dispatcher manual request"}
                />
                <Row
                  k="Distance Change"
                  v={`${truck.replanOutcome.original_distance_km ? `${truck.replanOutcome.original_distance_km} km → ` : ""}${truck.replanOutcome.new_distance_km ? `${truck.replanOutcome.new_distance_km} km` : ""} (+${(truck.replanOutcome.added_distance_km || 0).toFixed(1)} km detour)`}
                />
                <Row
                  k="Segments Reused"
                  v={`${truck.replanOutcome.segments_reused || 0} legs kept`}
                />
                <Row
                  k="Segments Changed"
                  v={`${truck.replanOutcome.segments_changed || 0} legs rerouted`}
                />
                {truck.replanOutcome.note && (
                  <Row k="System Note" v={truck.replanOutcome.note} />
                )}
              </div>
            </div>
          )}

          <div
            style={{
              marginTop: 10,
              padding: "8px 10px",
              background: "var(--surface)",
              borderRadius: 6,
              border: "1px solid var(--border)",
              fontSize: 11.5,
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "3px 0",
                color: "var(--text-secondary)",
              }}
            >
              <span>Vehicle Profile & Weight</span>
              <span style={{ color: "var(--emerald)", fontWeight: 500 }}>
                ✓ Feasible ({truck.vehicle || "LMV"})
              </span>
            </div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "3px 0",
                color: "var(--text-secondary)",
              }}
            >
              <span>Hazmat Restrictions</span>
              <span style={{ color: "var(--emerald)", fontWeight: 500 }}>
                ✓ Compliant ({truck.cargo?.hazmat_certified ? "Hazmat Certified" : "Standard Freight"})
              </span>
            </div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "3px 0",
                color: "var(--text-secondary)",
              }}
            >
              <span>Driver HOS / Rest Rules</span>
              <span
                style={{
                  color:
                    truck.telemetry?.driver_hours_today > 8
                      ? "var(--amber)"
                      : "var(--emerald)",
                  fontWeight: 500,
                }}
              >
                {truck.telemetry?.driver_hours_today > 8
                  ? "⚠ Near HOS limit"
                  : "✓ Within HOS limit"}
              </span>
            </div>
          </div>
        </section>

        {truck.telemetry ? (
          <section className="drawer-section">
            <h4>Live telemetry</h4>
            <div className="kv-list">
              <Row k="Fuel level" v={`${truck.telemetry.fuel_level}%`}
                tone={truck.telemetry.fuel_level < 20 ? "var(--rose)" : truck.telemetry.fuel_level < 40 ? "var(--amber)" : "var(--emerald)"} />
              <Row k="Current speed" v={`${truck.telemetry.current_speed} km/h`} />
              <Row k="Odometer" v={`${Math.round(truck.telemetry.odometer).toLocaleString()} km`} />
              <Row k="Tyre health" v={`${truck.telemetry.tyre_health}%`}
                tone={truck.telemetry.tyre_health < 75 ? "var(--amber)" : "var(--emerald)"} />
              <Row k="Engine health" v={`${truck.telemetry.engine_health}%`}
                tone={truck.telemetry.engine_health < 80 ? "var(--amber)" : "var(--emerald)"} />
              <Row k="Driver safety" v={`${truck.telemetry.driver_safety_score}/100`} />
              <Row k="Hours today" v={`${truck.telemetry.driver_hours_today} h`} />
              <Row k="Break due" v={truck.telemetry.break_due_at} />
              <Row k="Cargo temp" v={`${truck.telemetry.cargo_temperature}°C`} />
              <Row k="CO₂ emitted" v={`${truck.telemetry.co2_estimate} kg`} />
              <Row k="GPS fix" v={`${truck.telemetry.gps_fix_age}s ago`} />
            </div>
          </section>
        ) : (
          <section className="drawer-section">
            <h4>Not tracked in this dataset</h4>
            <p style={{ fontSize: 11.5, color: "var(--text-faint)", margin: "0 16px" }}>
              {(truck.untracked || []).join(", ").replace(/_/g, " ")}. Connect a
              telematics feed to populate them.
            </p>
          </section>
        )}
      </div>

      <footer className="drawer-actions">
        <button
          type="button"
          className="pill-btn"
          onClick={() => onAction(`Calling ${truck.driver}…`, "info")}
        >
          <Phone size={13} aria-hidden="true" /> Contact
        </button>
        <button
          type="button"
          className="pill-btn"
          onClick={() => onAction(`Message sent to ${truck.id}.`, "success")}
        >
          <MessageSquare size={13} aria-hidden="true" /> Message
        </button>
        <button
          type="button"
          className="pill-btn"
          onClick={() => {
            if (onReplan) {
              onReplan(truck);
            } else {
              onAction(`Re-route requested for ${truck.id}.`, "info");
            }
          }}
        >
          <RouteIcon size={13} aria-hidden="true" /> Re-route
        </button>
        <button
          type="button"
          className="pill-btn"
          onClick={() => onAction(`Shipment detail for ${truck.load}.`, "info")}
        >
          <Package size={13} aria-hidden="true" /> Shipment
        </button>
      </footer>
    </motion.aside>
  );
}
