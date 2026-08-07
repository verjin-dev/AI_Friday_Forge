export default function Header({ health, role, roles, onRoleChange }) {
  const graph = health?.knowledge_graph;
  const graphOk = graph?.ok;
  const llmOk = health?.llm?.configured;

  return (
    <header className="header">
      <div className="brand">
        <h1>LogiPilot AI</h1>
        <span className="tag">
          {health?.tagline || "Constraint-aware logistics intelligence"}
        </span>
      </div>

      <div className="status-row">
        <span className="pill" title={graph?.reason || ""}>
          <i className={`dot ${graphOk ? "ok" : "bad"}`} />
          Neo4j
          {graphOk ? ` · ${graph.node_count} nodes` : " · offline"}
        </span>

        <span className="pill" title={health?.llm?.model || ""}>
          <i className={`dot ${llmOk ? "ok" : "bad"}`} />
          LLM {llmOk ? `· ${health.llm.model}` : "· not configured"}
        </span>

        <span className="pill">
          <i
            className={`dot ${
              health?.observability?.langsmith_tracing ? "ok" : "warn"
            }`}
          />
          LangSmith
        </span>

        <span className="pill">
          <i className="dot ok" />
          {health?.tools?.registered ?? 0} tools
        </span>

        <select
          value={role}
          onChange={(event) => onRoleChange(event.target.value)}
          title="Role determines tool permissions and PII visibility"
        >
          {(roles.length ? roles : [{ name: role }]).map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </div>
    </header>
  );
}
