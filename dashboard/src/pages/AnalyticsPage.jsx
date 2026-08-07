import { useEffect, useState } from "react";
import { Info } from "lucide-react";

import MetricCard from "../components/MetricCard.jsx";
import PageState from "../components/PageState.jsx";
import { fetchKpis, fetchModelCard, fetchRunMetrics } from "../data/fleet.js";

const percent = (value) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(0)}%`;

export default function AnalyticsPage() {
  const [kpis, setKpis] = useState(null);
  const [runs, setRuns] = useState(null);
  const [card, setCard] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetchKpis(controller.signal),
      fetchRunMetrics(controller.signal),
      fetchModelCard(controller.signal),
    ])
      .then(([kpiPayload, runPayload, cardPayload]) => {
        setKpis(kpiPayload);
        setRuns(runPayload);
        setCard(cardPayload);
      })
      .catch((exc) => {
        if (exc.name !== "AbortError") setError(exc.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const measured = kpis?.measured;

  const cards = measured
    ? [
        {
          key: "route_compliance",
          label: "Route availability",
          value: percent(measured.compliant_route_availability),
          trend: `${measured.plans} plans`,
          note: "a compliant route existed",
          tone: "emerald",
        },
        {
          key: "open_alerts",
          label: "Violations prevented",
          value: String(measured.hard_violations_prevented),
          trend: `${measured.routes_disqualified} routes cut`,
          note: "blocked before dispatch",
          tone: "rose",
        },
        {
          key: "active_fleet",
          label: "Shortest was unsafe",
          value: percent(measured.shortest_route_unsafe_rate),
          trend: "of plans",
          note: "shortest option would have breached a constraint",
          tone: "amber",
        },
        {
          key: "distance_planned",
          label: "Avg diversion",
          value:
            measured.avg_diversion_km === null
              ? "—"
              : `${measured.avg_diversion_km} km`,
          trend: `${measured.avg_predicted_delay_minutes ?? "—"} min delay`,
          note: "cost of staying compliant",
          tone: "cyan",
        },
      ]
    : [];

  return (
    <PageState loading={loading} error={error}>
      <section className="metric-grid" aria-label="Business KPIs">
        {cards.map((metric, index) => (
          <MetricCard key={metric.key} metric={metric} index={index} />
        ))}
      </section>

      <section className="card">
        <div className="panel-head">
          <h3>Pending outcome data</h3>
          <span className="sub">
            {kpis?.outcomes_recorded ?? 0} arrivals recorded
          </span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Metric</th>
                <th>Value</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(kpis?.pending_outcome_data || {}).map(([key, value]) => (
                <tr key={key}>
                  <td className="cell-primary">{key.replace(/_/g, " ")}</td>
                  <td style={{ color: value === null ? "var(--text-faint)" : "var(--text)" }}>
                    {value === null ? "not measurable yet" : value}
                  </td>
                  <td className="cell-sub">
                    {key.includes("cost")
                      ? "needs a per-km fleet cost baseline"
                      : "needs actual arrival times"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {kpis?.notes?.map((note, index) => (
          <p className="provenance" key={index}>
            <Info size={13} aria-hidden="true" style={{ flex: "none", marginTop: 2 }} />
            {note}
          </p>
        ))}
      </section>

      <section className="split">
        <section className="card">
          <div className="panel-head">
            <h3>Agent runs</h3>
            <span className="sub">{runs?.runs ?? 0} recorded</span>
          </div>
          <div className="table-wrap">
            <table style={{ minWidth: 0 }}>
              <tbody>
                <tr>
                  <td className="cell-sub">Validation pass rate</td>
                  <td>{percent(runs?.pass_rate)}</td>
                </tr>
                <tr>
                  <td className="cell-sub">Average confidence</td>
                  <td>{percent(runs?.avg_confidence)}</td>
                </tr>
                <tr>
                  <td className="cell-sub">Average latency</td>
                  <td>{Math.round(runs?.avg_latency_ms ?? 0)} ms</td>
                </tr>
                <tr>
                  <td className="cell-sub">Total tokens</td>
                  <td>{(runs?.total_tokens ?? 0).toLocaleString()}</td>
                </tr>
                <tr>
                  <td className="cell-sub">Total cost</td>
                  <td>${(runs?.total_cost_usd ?? 0).toFixed(4)}</td>
                </tr>
                <tr>
                  <td className="cell-sub">Blocked by security</td>
                  <td>{runs?.blocked ?? 0}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section className="card">
          <div className="panel-head">
            <h3>Delay model</h3>
            <span className="sub">v{card?.version}</span>
          </div>
          <div style={{ padding: "12px 16px" }}>
            <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-secondary)" }}>
              {card?.rationale}
            </p>
            <h5
              style={{
                margin: "14px 0 6px",
                fontSize: 11,
                letterSpacing: "0.07em",
                textTransform: "uppercase",
                color: "var(--text-faint)",
              }}
            >
              Data sources
            </h5>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, lineHeight: 1.7 }}>
              {Object.entries(card?.data_sources || {}).map(([key, value]) => (
                <li key={key}>
                  <strong>{key}</strong> — {value}
                </li>
              ))}
            </ul>
            <h5
              style={{
                margin: "14px 0 6px",
                fontSize: 11,
                letterSpacing: "0.07em",
                textTransform: "uppercase",
                color: "var(--amber)",
              }}
            >
              Limitations
            </h5>
            <ul
              style={{
                margin: 0,
                paddingLeft: 18,
                fontSize: 12,
                color: "var(--text-secondary)",
                lineHeight: 1.7,
              }}
            >
              {(card?.limitations || []).map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </div>
        </section>
      </section>
    </PageState>
  );
}
