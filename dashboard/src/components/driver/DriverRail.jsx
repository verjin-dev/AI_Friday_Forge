import { LogOut, Package, Route, ShieldCheck, Truck } from "lucide-react";

export default function DriverRail({ session, truck, trucks, onSelect, onSignOut }) {
  return (
    <aside className="driver-rail" aria-label="Vehicle context">
      <div className="brand" style={{ padding: 0 }}>
        <span className="brand-mark" aria-hidden="true">
          <Truck size={16} strokeWidth={2} />
        </span>
        <span className="brand-text">
          <h3>LogiPilot</h3>
          <span style={{ fontSize: 11, color: "var(--text-faint)" }}>
            {session?.detail || "In-vehicle console"}
          </span>
        </span>
      </div>

      <label className="field" style={{ minWidth: 0 }}>
        <Route size={14} aria-hidden="true" style={{ color: "var(--cyan)" }} />
        <span className="sr-only">Active vehicle</span>
        <select
          value={truck?.id || ""}
          onChange={(event) => onSelect(event.target.value)}
          style={{ flex: 1, background: "none", border: "none", outline: "none" }}
        >
          {trucks.map((item) => (
            <option key={item.id} value={item.id}>
              {item.id} · {item.route}
            </option>
          ))}
        </select>
      </label>

      <div className="rail-block">
        <div className="caption">Route progress</div>
        <div className="value">{truck?.progress ?? 0}%</div>
        <div className="bar" style={{ marginTop: 6 }}>
          <div style={{ width: `${truck?.progress ?? 0}%` }} />
        </div>
        <div className="sub">
          {truck?.distance_covered_km ?? 0} of {truck?.distanceKm ?? 0} km
        </div>
      </div>

      <div className="rail-block">
        <div className="caption">Status</div>
        <div className="value" style={{ fontSize: 14 }}>
          {truck?.status || "—"}
        </div>
        <div className="sub">
          {truck?.feasible
            ? "Route compliant"
            : truck?.hardViolations?.[0] || "Not compliant"}
        </div>
      </div>

      <div className="rail-block">
        <div className="caption">Cargo</div>
        <div className="value" style={{ fontSize: 14 }}>
          <Package size={13} aria-hidden="true" /> {truck?.load || "—"}
        </div>
        <div className="sub">
          {truck?.cargo?.capacity_kg
            ? `${truck.cargo.capacity_kg} kg capacity`
            : "capacity unknown"}
        </div>
      </div>

      <div className="rail-block">
        <div className="caption">Vehicle</div>
        <div className="value" style={{ fontSize: 13 }}>
          {truck?.vehicle || "unassigned"}
        </div>
        <div className="sub">{truck?.driver}</div>
      </div>

      <p
        style={{
          fontSize: 11,
          color: "var(--text-faint)",
          display: "flex",
          gap: 6,
          margin: 0,
          lineHeight: 1.5,
        }}
      >
        <ShieldCheck size={12} aria-hidden="true" style={{ flex: "none", marginTop: 1 }} />
        Fuel, speed and driver scores are not tracked in this dataset.
      </p>

      <button
        type="button"
        className="pill-btn"
        onClick={onSignOut}
        style={{ marginTop: "auto", justifyContent: "center" }}
      >
        <LogOut size={14} aria-hidden="true" />
        Sign out
      </button>
    </aside>
  );
}
