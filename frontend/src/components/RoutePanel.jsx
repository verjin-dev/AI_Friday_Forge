function RiskBadge({ risk }) {
  const cls =
    risk === "severe" || risk === "high"
      ? "bad"
      : risk === "moderate"
      ? "warn"
      : "ok";
  return <span className={`badge ${cls}`}>{risk} delay risk</span>;
}

function CheckRow({ check }) {
  const cls = check.satisfied
    ? "pass"
    : check.severity === "hard"
    ? "fail"
    : "soft";
  const verdict = check.satisfied ? "PASS" : check.severity === "hard" ? "FAIL" : "WARN";
  return (
    <div className={`check ${cls}`}>
      <span className="verdict">{verdict}</span>
      <span className="code">{check.code}</span>
      <span className="detail">{check.detail}</span>
    </div>
  );
}

export default function RoutePanel({ plan, selected, onSelect, loading }) {
  if (loading) {
    return (
      <div className="empty">
        <span className="spin" /> Planning routes…
      </div>
    );
  }

  if (!plan) {
    return (
      <div className="empty">
        Ask for a route, or use the planner above, to see constraint verdicts
        and delay predictions.
      </div>
    );
  }

  return (
    <div>
      {plan.alerts?.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          {plan.alerts.map((alert, index) => (
            <div
              key={index}
              className={`badge ${alert.level === "critical" ? "bad" : "warn"}`}
              style={{ display: "block", marginBottom: 6, padding: "7px 11px" }}
            >
              {alert.message}
            </div>
          ))}
        </div>
      )}

      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>
        {plan.origin} → {plan.destination} · {plan.feasible_count} of{" "}
        {plan.route_count} routes compliant
        {plan.all_infeasible && " · no compliant option exists"}
      </p>

      {plan.routes.map((route) => {
        const isSelected = selected?.label === route.label;
        const isRecommended = plan.recommended_label === route.label;

        return (
          <div
            key={route.label}
            className={`route ${route.feasible ? "feasible" : "infeasible"}`}
            onClick={() => onSelect(route)}
            style={{
              cursor: "pointer",
              outline: isSelected ? "1px solid var(--accent)" : "none",
            }}
          >
            <div className="route-head">
              <div>
                <div className="route-title">
                  {route.label}
                  {isRecommended && (
                    <span className="badge ok" style={{ marginLeft: 8 }}>
                      recommended
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 3 }}>
                  {route.total_distance_km} km ·{" "}
                  {route.delay.predicted_total_minutes} min ETA (
                  {route.delay.free_flow_minutes} free-flow +{" "}
                  {route.delay.predicted_delay_minutes} delay)
                </div>
              </div>
              <div style={{ display: "flex", gap: 6, flexDirection: "column" }}>
                <span className={`badge ${route.feasible ? "ok" : "bad"}`}>
                  {route.feasible ? "compliant" : "disqualified"}
                </span>
                <RiskBadge risk={route.delay.risk} />
              </div>
            </div>

            {isSelected && (
              <>
                <div className="checks">
                  {route.constraint_report.checks.map((check) => (
                    <CheckRow key={check.code} check={check} />
                  ))}
                </div>

                {route.delay.factors.length > 0 && (
                  <div className="unverified">
                    <strong style={{ color: "var(--text)" }}>
                      Delay factors
                    </strong>
                    {route.delay.factors.map((factor, index) => (
                      <div key={index} style={{ marginTop: 3 }}>
                        +{factor.minutes} min — {factor.name}
                        <br />
                        <span style={{ opacity: 0.75 }}>{factor.evidence}</span>
                      </div>
                    ))}
                    <div style={{ marginTop: 6 }}>
                      Prediction confidence:{" "}
                      {(route.delay.confidence * 100).toFixed(0)}%
                      <div className="bar">
                        <div
                          style={{ width: `${route.delay.confidence * 100}%` }}
                        />
                      </div>
                    </div>
                  </div>
                )}

                {route.constraint_report.unverifiable.length > 0 && (
                  <div className="unverified">
                    Not verifiable from available data:{" "}
                    {route.constraint_report.unverifiable.join(", ")}
                  </div>
                )}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
