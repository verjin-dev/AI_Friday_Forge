import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Activity, BrainCircuit, Route, Truck } from "lucide-react";

import { fetchHealth } from "../../data/fleet.js";
import LogisticsScene from "./LogisticsScene.jsx";

export default function LoginVisual() {
  const [health, setHealth] = useState(null);
  const [phase, setPhase] = useState("enroute");

  useEffect(() => {
    const controller = new AbortController();
    fetchHealth(controller.signal)
      .then(setHealth)
      .catch(() => setHealth(null));
    return () => controller.abort();
  }, []);

  const graph = health?.knowledge_graph;

  const stats = [
    { label: "Active Vehicles", value: "184" },
    { label: "On-Time Delivery", value: "94.7%" },
    { label: "AI Reroutes Today", value: "23" },
  ];

  const phaseLabel = {
    enroute: "Enroute",
    risk: "Delay risk detected",
    rerouting: "AI rerouting",
    optimized: "Optimized ETA",
  }[phase];

  return (
    <section className="login-visual">
      <div className="login-brand">
        <span className="brand-mark" aria-hidden="true">
          <Truck size={17} strokeWidth={2} />
        </span>
        <div>
          <h1>LogiPilot Ai</h1>
          <span>Intelligent Logistics Control Tower</span>
        </div>
      </div>

      <div className="login-badge-row">
        <span className="platform-badge">
          <BrainCircuit size={13} aria-hidden="true" />
          AI-Powered Logistics
        </span>
        <span>Route Optimization</span>
        <span>Delay Prediction</span>
        <span>Fleet Intelligence</span>
      </div>

      <motion.div
        className="login-copy"
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
      >
        <h2>Smarter Routes. Predictable Deliveries.</h2>
        <p>
          AI-powered fleet intelligence that predicts delays, optimizes routes,
          and keeps every delivery moving.
        </p>
      </motion.div>

      <motion.div
        className="visual-shell"
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.42, delay: 0.08, ease: "easeOut" }}
      >
        <div className="route-insight route-insight-floating">
          <span>
            <BrainCircuit size={15} />
          </span>
          <div>
            <strong>12 potential delays prevented today</strong>
            <small>{phaseLabel}</small>
          </div>
        </div>

        <div className="logistics-map">
          <LogisticsScene onPhaseChange={setPhase} />
        </div>

        <div className="intelligence-card">
          <div className="intelligence-title">
            <span>
              <Activity size={15} aria-hidden="true" />
            </span>
            <div>
              <strong>AI Route Intelligence</strong>
              <small>
                {graph?.ok ? "Knowledge graph online" : "Monitoring network status"}
              </small>
            </div>
          </div>

          <div className="login-metrics">
            {stats.map((metric) => (
              <div className="login-metric" key={metric.label}>
                <div className="value">{metric.value}</div>
                <div className="label">{metric.label}</div>
              </div>
            ))}
          </div>

          <div className="system-line">
            <Route size={13} aria-hidden="true" />
            <span>
              {graph?.node_count != null
                ? `${graph.node_count} graph nodes connected`
                : "Live logistics intelligence ready"}
            </span>
          </div>
        </div>
      </motion.div>
    </section>
  );
}
