import { CornerUpRight, Navigation } from "lucide-react";

export default function NavigationOverlay({ truck }) {
  if (!truck) return null;

  const arrival = truck.etaMinutes
    ? new Date(Date.now() + truck.etaMinutes * 60000).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })
    : truck.eta;

  return (
    <div className="nav-overlay">
      <div className="manoeuvre">
        <span className="manoeuvre-icon" aria-hidden="true">
          <CornerUpRight size={22} strokeWidth={2} />
        </span>
        <span style={{ minWidth: 0 }}>
          <span className="distance">
            {truck.next_stop ? `Next: ${truck.next_stop}` : "Route complete"}
          </span>
          <span className="instruction">
            {truck.distance_remaining_km != null
              ? `${truck.distance_remaining_km} km remaining on ${truck.route}`
              : truck.route}
          </span>
        </span>
      </div>

      <div className="journey-stats">
        <div className="journey-stat">
          <div className="k">ETA</div>
          <div className="v">{arrival || "—"}</div>
        </div>
        <div className="journey-stat">
          <div className="k">Remaining</div>
          <div className="v">
            {truck.distance_remaining_km != null
              ? `${Math.round(truck.distance_remaining_km)} km`
              : "—"}
          </div>
        </div>
        <div className="journey-stat">
          <div className="k">Delay</div>
          <div
            className="v"
            style={{
              color: truck.delayMinutes > 0 ? "var(--amber)" : "var(--emerald)",
            }}
          >
            {truck.delayMinutes ? `+${Math.round(truck.delayMinutes)}m` : "none"}
          </div>
        </div>
        <div className="journey-stat">
          <div className="k">Source</div>
          <div className="v" style={{ fontSize: 12.5 }}>
            <Navigation size={11} aria-hidden="true" />{" "}
            {truck.liveTraffic ? "live" : "graph"}
          </div>
        </div>
      </div>
    </div>
  );
}
