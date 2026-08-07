function Stat({ label, value, hint }) {
  return (
    <div
      style={{
        border: "1px solid var(--line)",
        borderRadius: 8,
        padding: "10px 12px",
      }}
    >
      <div style={{ fontSize: 11, color: "var(--muted)", letterSpacing: 0.3 }}>
        {label}
      </div>
      <div style={{ fontSize: 20, fontWeight: 600, marginTop: 2 }}>{value}</div>
      {hint && (
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
          {hint}
        </div>
      )}
    </div>
  );
}

export default function MetricsPanel({ metrics, response, modelCard }) {
  const run = response?.metrics;

  return (
    <div>
      <h3 style={{ fontSize: 13, margin: "0 0 8px" }}>This run</h3>
      {run ? (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
            gap: 8,
          }}
        >
          <Stat label="LATENCY" value={`${Math.round(run.total_latency_ms)} ms`} />
          <Stat
            label="TOKENS"
            value={run.prompt_tokens + run.completion_tokens}
            hint={`${run.prompt_tokens} in / ${run.completion_tokens} out`}
          />
          <Stat label="LLM CALLS" value={run.llm_calls} />
          <Stat label="TOOL CALLS" value={run.tool_calls} />
          <Stat label="GRAPH QUERIES" value={run.graph_queries} />
          <Stat label="COST" value={`$${run.estimated_cost_usd.toFixed(4)}`} />
          <Stat label="RETRY LOOPS" value={run.reflection_loops} />
        </div>
      ) : (
        <div className="empty">No run yet.</div>
      )}

      <h3 style={{ fontSize: 13, margin: "20px 0 8px" }}>Platform (all runs)</h3>
      {metrics && metrics.runs > 0 ? (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
              gap: 8,
            }}
          >
            <Stat label="RUNS" value={metrics.runs} />
            <Stat
              label="VALIDATION PASS RATE"
              value={
                metrics.pass_rate === null
                  ? "—"
                  : `${(metrics.pass_rate * 100).toFixed(0)}%`
              }
              hint="answers that survived validation"
            />
            <Stat
              label="AVG CONFIDENCE"
              value={
                metrics.avg_confidence === null
                  ? "—"
                  : `${(metrics.avg_confidence * 100).toFixed(0)}%`
              }
            />
            <Stat
              label="AVG LATENCY"
              value={`${Math.round(metrics.avg_latency_ms)} ms`}
            />
            <Stat label="TOTAL TOKENS" value={metrics.total_tokens} />
            <Stat
              label="TOTAL COST"
              value={`$${metrics.total_cost_usd.toFixed(4)}`}
            />
            <Stat label="BLOCKED" value={metrics.blocked} hint="by security" />
          </div>

          {metrics.slowest_agents?.length > 0 && (
            <>
              <h3 style={{ fontSize: 13, margin: "20px 0 8px" }}>
                Slowest agents
              </h3>
              <table className="kv">
                <tbody>
                  {metrics.slowest_agents.map((item) => (
                    <tr key={item.agent}>
                      <td>{item.agent.replace(/_/g, " ")}</td>
                      <td>{Math.round(item.avg_latency_ms)} ms avg</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      ) : (
        <div className="empty">No completed runs recorded yet.</div>
      )}

      {modelCard && (
        <>
          <h3 style={{ fontSize: 13, margin: "20px 0 8px" }}>
            Delay model card
          </h3>
          <table className="kv">
            <tbody>
              <tr>
                <td>Model</td>
                <td>{modelCard.name}</td>
              </tr>
              <tr>
                <td>Type</td>
                <td>{modelCard.type}</td>
              </tr>
            </tbody>
          </table>
          <div
            style={{
              fontSize: 12,
              color: "var(--muted)",
              marginTop: 8,
              lineHeight: 1.5,
            }}
          >
            {modelCard.rationale}
          </div>
          <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>
            <strong style={{ color: "var(--warn)" }}>Limitations</strong>
            <ul style={{ margin: "4px 0", paddingLeft: 18 }}>
              {modelCard.limitations.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
