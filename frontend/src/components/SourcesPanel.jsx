export default function SourcesPanel({ response }) {
  const sources = response?.explanation?.sources || [];
  const validation = response?.validation;
  const security = response?.security;

  if (!response) {
    return <div className="empty">Sources and validation appear after a run.</div>;
  }

  return (
    <div>
      {validation && (
        <div style={{ marginBottom: 18 }}>
          <h3 style={{ fontSize: 13, margin: "0 0 8px" }}>Validation</h3>
          <table className="kv">
            <tbody>
              <tr>
                <td>Verdict</td>
                <td>
                  <span className={`badge ${validation.passed ? "ok" : "bad"}`}>
                    {validation.passed ? "passed" : "failed"}
                  </span>
                </td>
              </tr>
              <tr>
                <td>Confidence</td>
                <td>
                  {(validation.confidence * 100).toFixed(0)}%
                  <div className="bar">
                    <div style={{ width: `${validation.confidence * 100}%` }} />
                  </div>
                </td>
              </tr>
              <tr>
                <td>Claims grounded</td>
                <td>
                  {validation.grounded_claims} / {validation.total_claims}
                </td>
              </tr>
            </tbody>
          </table>

          {validation.issues?.length > 0 && (
            <div style={{ marginTop: 10 }}>
              {validation.issues.map((issue, index) => (
                <div
                  key={index}
                  className="source"
                  style={{ borderColor: "var(--warn)" }}
                >
                  <div className="origin">{issue.kind}</div>
                  {issue.detail}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {security?.findings?.length > 0 && (
        <div style={{ marginBottom: 18 }}>
          <h3 style={{ fontSize: 13, margin: "0 0 8px" }}>Security</h3>
          {security.findings.map((finding, index) => (
            <div key={index} className="source">
              <div className="origin">{finding.severity}</div>
              <strong>{finding.check}</strong>
              <div style={{ color: "var(--muted)" }}>{finding.detail}</div>
            </div>
          ))}
        </div>
      )}

      <h3 style={{ fontSize: 13, margin: "0 0 8px" }}>
        Sources ({sources.length})
      </h3>
      {sources.length === 0 && (
        <div className="empty">No sources were attached to this answer.</div>
      )}
      {sources.map((source, index) => (
        <div key={index} className="source">
          <div className="origin">{source.origin}</div>
          <strong>{source.label}</strong>
          {source.detail && (
            <div style={{ color: "var(--muted)", fontSize: 12 }}>
              {source.detail}
            </div>
          )}
          {source.url && (
            <a href={source.url} target="_blank" rel="noreferrer">
              {source.url}
            </a>
          )}
        </div>
      ))}
    </div>
  );
}
