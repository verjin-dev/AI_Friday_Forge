import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

import PageState from "../components/PageState.jsx";
import {
  fetchConstraintCatalogue,
  fetchConstraintProfile,
  fetchHealth,
  fetchTools,
} from "../data/fleet.js";

function Row({ label, value, hint }) {
  return (
    <tr>
      <td className="cell-sub">{label}</td>
      <td>
        {value}
        {hint && <div className="cell-sub">{hint}</div>}
      </td>
    </tr>
  );
}

export default function SettingsPage({ theme, onToggleTheme }) {
  const [health, setHealth] = useState(null);
  const [profile, setProfile] = useState(null);
  const [catalogue, setCatalogue] = useState("");
  const [tools, setTools] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetchHealth(controller.signal),
      fetchConstraintProfile(controller.signal),
      fetchConstraintCatalogue(controller.signal),
      fetchTools(controller.signal),
    ])
      .then(([healthPayload, profilePayload, cataloguePayload, toolPayload]) => {
        setHealth(healthPayload);
        setProfile(profilePayload);
        setCatalogue(cataloguePayload.catalogue);
        setTools(toolPayload);
      })
      .catch((exc) => {
        if (exc.name !== "AbortError") setError(exc.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  return (
    <PageState loading={loading} error={error}>
      <section className="split">
        <section className="card">
          <div className="panel-head">
            <h3>Platform status</h3>
            <span className="sub">{health?.environment}</span>
          </div>
          <div className="table-wrap">
            <table style={{ minWidth: 0 }}>
              <tbody>
                <Row
                  label="Knowledge graph"
                  value={health?.knowledge_graph?.ok ? "connected" : "offline"}
                  hint={`${health?.knowledge_graph?.node_count ?? 0} nodes · ${
                    health?.knowledge_graph?.labels ?? 0
                  } labels · ${health?.knowledge_graph?.schema_source}`}
                />
                <Row
                  label="LLM gateway"
                  value={health?.llm?.configured ? "configured" : "not configured"}
                  hint={health?.llm?.model}
                />
                <Row
                  label="LangSmith tracing"
                  value={
                    health?.observability?.langsmith_tracing ? "on" : "off"
                  }
                  hint={health?.observability?.project}
                />
                <Row
                  label="Tools registered"
                  value={health?.tools?.registered ?? 0}
                  hint={
                    tools?.tools
                      ?.slice(0, 4)
                      .map((tool) => tool.name)
                      .join(", ") + "…"
                  }
                />
                <Row
                  label="Reflection loops"
                  value={health?.workflow?.max_reflection_loops}
                  hint={`confidence threshold ${health?.workflow?.confidence_threshold}`}
                />
              </tbody>
            </table>
          </div>
        </section>

        <section className="card">
          <div className="panel-head">
            <h3>Appearance</h3>
          </div>
          <div style={{ padding: "14px 16px" }}>
            <button type="button" className="pill-btn" onClick={onToggleTheme}>
              {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
              Switch to {theme === "dark" ? "light" : "dark"} theme
            </button>
            <p
              style={{
                fontSize: 12,
                color: "var(--text-secondary)",
                marginTop: 12,
                marginBottom: 0,
              }}
            >
              The theme only changes design tokens. It does not affect routing,
              constraint evaluation or any data shown.
            </p>
          </div>
        </section>
      </section>

      <section className="card">
        <div className="panel-head">
          <h3>Constraint limits</h3>
          <span className="sub">logistics_constraints.json</span>
        </div>
        <div className="table-wrap">
          <table>
            <tbody>
              {Object.entries(profile || {}).map(([key, value]) => (
                <tr key={key}>
                  <td className="cell-sub">{key.replace(/_/g, " ")}</td>
                  <td>{value === null ? "—" : String(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="provenance">
          These are read-only here. Edit <code>logistics_constraints.json</code>{" "}
          at the project root and restart the backend to change them.
        </p>
      </section>

      <section className="card">
        <div className="panel-head">
          <h3>Rule catalogue</h3>
          <span className="sub">what is enforced</span>
        </div>
        <pre
          style={{
            margin: 0,
            padding: "14px 16px",
            fontSize: 11.5,
            lineHeight: 1.65,
            color: "var(--text-secondary)",
            overflowX: "auto",
            fontFamily: "ui-monospace, monospace",
          }}
        >
          {catalogue}
        </pre>
      </section>
    </PageState>
  );
}
