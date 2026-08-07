import { useCallback, useEffect, useState } from "react";
import Header from "./components/Header.jsx";
import Chat from "./components/Chat.jsx";
import MapView from "./components/MapView.jsx";
import Planner from "./components/Planner.jsx";
import RoutePanel from "./components/RoutePanel.jsx";
import AgentTimeline from "./components/AgentTimeline.jsx";
import SourcesPanel from "./components/SourcesPanel.jsx";
import MetricsPanel from "./components/MetricsPanel.jsx";
import { getHealth, getMetrics, getRoles, streamChat } from "./api.js";

const TABS = ["Map", "Routes", "Timeline", "Sources", "Metrics"];

export default function App() {
  const [health, setHealth] = useState(null);
  const [roles, setRoles] = useState([]);
  const [role, setRole] = useState("ops_manager");

  const [messages, setMessages] = useState([]);
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState([]);
  const [response, setResponse] = useState(null);
  const [sessionId, setSessionId] = useState(null);

  const [network, setNetwork] = useState(null);
  const [plan, setPlan] = useState(null);
  const [selectedRoute, setSelectedRoute] = useState(null);
  const [planning, setPlanning] = useState(false);
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [busyIncident, setBusyIncident] = useState(null);

  const [metrics, setMetrics] = useState(null);
  const [modelCard, setModelCard] = useState(null);
  const [tab, setTab] = useState("Map");
  const [error, setError] = useState(null);

  const loadNetwork = useCallback(async () => {
    try {
      const data = await fetch("/api/routes/network").then((r) => r.json());
      setNetwork(data);
      if (!origin && data.locations?.length) {
        setOrigin(data.locations[0].name);
        setDestination(data.locations[data.locations.length - 1].name);
      }
    } catch (exc) {
      setError(`Could not load the road network: ${exc.message}`);
    }
  }, [origin]);

  useEffect(() => {
    getHealth().then(setHealth).catch((exc) => setError(exc.message));
    getRoles().then(setRoles).catch(() => {});
    getMetrics().then(setMetrics).catch(() => {});
    fetch("/api/routes/model-card")
      .then((r) => r.json())
      .then(setModelCard)
      .catch(() => {});
    loadNetwork();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runPlan = useCallback(async () => {
    if (!origin || !destination) return;
    setPlanning(true);
    setError(null);
    try {
      const data = await fetch(
        `/api/routes/plan?origin=${encodeURIComponent(
          origin
        )}&destination=${encodeURIComponent(destination)}`
      ).then(async (r) => {
        if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
        return r.json();
      });
      setPlan(data);
      const recommended =
        data.routes.find((item) => item.label === data.recommended_label) ||
        data.routes[0];
      setSelectedRoute(recommended || null);
      setTab("Map");
    } catch (exc) {
      setError(exc.message);
      setPlan(null);
    } finally {
      setPlanning(false);
    }
  }, [origin, destination]);

  const toggleIncident = useCallback(
    async (incidentId, status) => {
      setBusyIncident(incidentId);
      try {
        const data = await fetch("/api/routes/scenario", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ incident_id: incidentId, status }),
        }).then((r) => r.json());
        setNetwork(data.network);
        // Re-plan immediately so the route change is visible.
        if (origin && destination) await runPlan();
      } catch (exc) {
        setError(exc.message);
      } finally {
        setBusyIncident(null);
      }
    },
    [origin, destination, runPlan]
  );

  const send = useCallback(
    async (text) => {
      setMessages((current) => [...current, { role: "user", content: text }]);
      setRunning(true);
      setSteps([]);
      setResponse(null);
      setError(null);
      setTab("Timeline");

      const history = messages.slice(-6).map((message) => ({
        role: message.role,
        content: message.content,
      }));

      try {
        await streamChat(
          { message: text, sessionId, role, history },
          (event) => {
            if (event.event === "start") {
              setSessionId(event.session_id);
            } else if (event.event === "agent") {
              setSteps((current) => [...current, event]);
            } else if (event.event === "complete") {
              const payload = event.response;
              setResponse(payload);
              setMessages((current) => [
                ...current,
                {
                  role: "assistant",
                  content: payload.answer,
                  blocked: payload.blocked,
                  meta: {
                    confidence: payload.validation?.confidence,
                    sources: payload.explanation?.sources?.length ?? 0,
                    latency: payload.metrics?.total_latency_ms,
                    langsmith: payload.langsmith_url,
                  },
                },
              ]);
              getMetrics().then(setMetrics).catch(() => {});
            } else if (event.event === "error") {
              setError(event.message);
            }
          }
        );
      } catch (exc) {
        setError(exc.message);
      } finally {
        setRunning(false);
      }
    },
    [messages, role, sessionId]
  );

  const locationNames = (network?.locations || []).map((item) => item.name);

  return (
    <div className="app">
      <Header
        health={health}
        role={role}
        roles={roles}
        onRoleChange={setRole}
      />

      {error && (
        <div
          style={{
            background: "#2a1518",
            borderBottom: "1px solid var(--bad)",
            color: "var(--bad)",
            padding: "8px 16px",
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      <div className="body">
        <Chat messages={messages} onSend={send} running={running} />

        <section className="panel">
          <Planner
            locations={locationNames}
            origin={origin}
            destination={destination}
            onOriginChange={setOrigin}
            onDestinationChange={setDestination}
            onPlan={runPlan}
            planning={planning}
            incidents={network?.incidents || []}
            onToggleIncident={toggleIncident}
            busyIncident={busyIncident}
          />

          <div className="tabs">
            {TABS.map((name) => (
              <button
                key={name}
                className={tab === name ? "active" : ""}
                onClick={() => setTab(name)}
              >
                {name}
                {name === "Timeline" && steps.length > 0 && ` (${steps.length})`}
                {name === "Routes" && plan && ` (${plan.route_count})`}
              </button>
            ))}
          </div>

          <div className="tab-body">
            {tab === "Map" && (
              <>
                <MapView
                  network={network}
                  plan={plan}
                  selectedRoute={selectedRoute}
                />
                {selectedRoute && (
                  <div style={{ marginTop: 12, fontSize: 13 }}>
                    <strong>{selectedRoute.label}</strong>
                    <div style={{ color: "var(--muted)", marginTop: 4 }}>
                      {selectedRoute.legs
                        .map((leg) => leg.description)
                        .join("  ·  ")}
                    </div>
                  </div>
                )}
              </>
            )}

            {tab === "Routes" && (
              <RoutePanel
                plan={plan}
                selected={selectedRoute}
                onSelect={setSelectedRoute}
                loading={planning}
              />
            )}

            {tab === "Timeline" && (
              <AgentTimeline steps={steps} running={running} />
            )}

            {tab === "Sources" && <SourcesPanel response={response} />}

            {tab === "Metrics" && (
              <MetricsPanel
                metrics={metrics}
                response={response}
                modelCard={modelCard}
              />
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
