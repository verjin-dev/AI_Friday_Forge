import { CornerUpRight, Navigation } from "lucide-react";

const SLA_TONE = {
  on_time: "var(--emerald)",
  at_risk: "var(--amber)",
  late: "var(--rose)",
};

const SLA_LABEL = {
  on_time: "on time",
  at_risk: "at risk",
  late: "running late",
};

export default function NavigationOverlay({ truck }) {
  if (!truck) return null;

  // The backend already projects arrival from the distance still to run at the
  // measured corridor speed, plus the delay ahead. This used to add the *whole*
  // journey time to the current clock, which put arrival hours out for a vehicle
  // most of the way along its route.
  const arrival = truck.eta;
  const tone = SLA_TONE[truck.slaStatus];

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
          <div className="v" style={tone ? { color: tone } : undefined}>
            {arrival || "—"}
          </div>
        </div>
        <div className="journey-stat">
          <div className="k">
            {truck.deliverBy && truck.deliverBy !== "—"
              ? `Due ${truck.deliverBy}`
              : "Due"}
          </div>
          <div
            className="v"
            style={{ ...(tone ? { color: tone } : {}), fontSize: 12.5 }}
          >
            {truck.slaStatus
              ? `${SLA_LABEL[truck.slaStatus]}${
                  truck.slaDeltaMinutes == null
                    ? ""
                    : ` ${Math.abs(Math.round(truck.slaDeltaMinutes))}m`
                }`
              : "—"}
          </div>
        </div>
        <div className="journey-stat">
          <div className="k">Remaining</div>
          <div className="v">
            {truck.etaRemainingKm != null
              ? `${Math.round(truck.etaRemainingKm)} km`
              : "—"}
          </div>
        </div>
        <div className="journey-stat">
          {/* Delay still ahead of the vehicle, not the whole journey's — what is
              already driven past cannot make this arrival any later. */}
          <div className="k">Delay ahead</div>
          <div
            className="v"
            style={{
              color:
                truck.etaBufferMinutes > 0 ? "var(--amber)" : "var(--emerald)",
            }}
          >
            {truck.etaBufferMinutes
              ? `+${Math.round(truck.etaBufferMinutes)}m`
              : "none"}
          </div>
        </div>
        <div className="journey-stat">
          <div className="k">Speed</div>
          <div className="v" style={{ fontSize: 12.5 }}>
            <Navigation size={11} aria-hidden="true" />{" "}
            {truck.etaSpeedKmh
              ? `${Math.round(truck.etaSpeedKmh)} km/h ${
                  truck.etaSpeedSource === "live" ? "live" : "graph"
                }`
              : truck.liveTraffic
              ? "live"
              : "graph"}
          </div>
        </div>
      </div>
    </div>
  );
}
