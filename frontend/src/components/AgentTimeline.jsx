const ICONS = {
  completed: "✓",
  failed: "✕",
  skipped: "–",
  running: "",
  pending: "",
};

export default function AgentTimeline({ steps, running }) {
  if (!steps.length && !running) {
    return (
      <div className="empty">
        The agent timeline appears here as the workflow runs.
      </div>
    );
  }

  return (
    <div>
      {steps.map((step, index) => (
        <div className="step" key={`${step.agent}-${index}`}>
          <div className={`step-dot ${step.status}`}>
            {ICONS[step.status] ?? ""}
          </div>
          <div>
            <div className="step-name">
              {step.agent.replace(/_/g, " ")}
              <span className="step-meta">
                {step.latency_ms ? `${Math.round(step.latency_ms)} ms` : ""}
                {step.tokens ? ` · ${step.tokens} tok` : ""}
              </span>
            </div>
            <div className="step-summary">{step.summary}</div>
            {step.error && (
              <div className="step-summary" style={{ color: "var(--bad)" }}>
                {step.error}
              </div>
            )}
            {step.detail?.constraint_report && (
              <pre
                style={{
                  fontSize: 11,
                  color: "var(--muted)",
                  background: "#0c1219",
                  padding: 8,
                  borderRadius: 6,
                  overflowX: "auto",
                  marginTop: 6,
                }}
              >
                {step.detail.constraint_report}
              </pre>
            )}
          </div>
        </div>
      ))}

      {running && (
        <div className="step">
          <div className="step-dot running">
            <span className="spin" />
          </div>
          <div>
            <div className="step-name">working…</div>
          </div>
        </div>
      )}
    </div>
  );
}
