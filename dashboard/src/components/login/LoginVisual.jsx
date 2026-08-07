import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Truck } from "lucide-react";

import { fetchHealth } from "../../data/fleet.js";

/**
 * Animated logistics scene.
 *
 * Rendered as perspective SVG rather than WebGL: it conveys the same
 * corridor/node language, costs no extra dependency, and stays smooth on the
 * low-powered displays this product also targets. On small screens the caller
 * hides the metric cards and the scene simplifies with it.
 */
function Scene({ reduced }) {
  const nodes = [
    { x: 120, y: 300 },
    { x: 260, y: 220 },
    { x: 400, y: 265 },
    { x: 540, y: 175 },
    { x: 660, y: 235 },
  ];

  const path = nodes
    .map((point, index) => `${index === 0 ? "M" : "L"}${point.x},${point.y}`)
    .join(" ");

  const alternate = "M120,300 L280,350 L430,330 L560,255 L660,235";

  return (
    <div className="scene" aria-hidden="true">
      <div className="scene-glow" />
      <svg viewBox="0 0 780 460" preserveAspectRatio="xMidYMid slice">
        <defs>
          <linearGradient id="routeMain" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#56d8ee" stopOpacity="0.25" />
            <stop offset="55%" stopColor="#56d8ee" stopOpacity="0.95" />
            <stop offset="100%" stopColor="#7886ff" stopOpacity="0.5" />
          </linearGradient>
        </defs>

        {/* perspective ground grid */}
        {Array.from({ length: 11 }).map((_, index) => (
          <line
            key={`h-${index}`}
            x1="0"
            y1={250 + index * index * 2.1}
            x2="780"
            y2={250 + index * index * 2.1}
            stroke="rgba(255,255,255,0.05)"
            strokeWidth="1"
          />
        ))}
        {Array.from({ length: 15 }).map((_, index) => (
          <line
            key={`v-${index}`}
            x1={390 + (index - 7) * 26}
            y1="250"
            x2={390 + (index - 7) * 150}
            y2="460"
            stroke="rgba(255,255,255,0.045)"
            strokeWidth="1"
          />
        ))}

        {/* alternate corridor, muted */}
        <path
          d={alternate}
          fill="none"
          stroke="#5b7186"
          strokeWidth="2.5"
          strokeDasharray="6 8"
          opacity="0.55"
        />

        {/* primary corridor */}
        <path
          d={path}
          fill="none"
          stroke="url(#routeMain)"
          strokeWidth="3.5"
          strokeLinecap="round"
          className={reduced ? undefined : "route-dash"}
        />

        {nodes.map((point, index) => (
          <g key={index}>
            <circle
              cx={point.x}
              cy={point.y}
              r="6"
              fill="#56d8ee"
              opacity="0.5"
              className={reduced ? undefined : "node-pulse"}
              style={{ animationDelay: `${index * 0.45}s` }}
            />
            <circle cx={point.x} cy={point.y} r="3" fill="#eff7fa" />
          </g>
        ))}

        {/* destination emphasis */}
        <circle cx={660} cy={235} r="12" fill="none" stroke="#51d29d" strokeWidth="2" opacity="0.8" />
      </svg>
    </div>
  );
}

export default function LoginVisual() {
  const [health, setHealth] = useState(null);
  const reduced =
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  useEffect(() => {
    const controller = new AbortController();
    fetchHealth(controller.signal)
      .then(setHealth)
      .catch(() => setHealth(null));
    return () => controller.abort();
  }, []);

  const graph = health?.knowledge_graph;

  const metrics = [
    {
      label: "Network",
      value: graph?.node_count != null ? String(graph.node_count) : "—",
      note: graph?.ok ? "graph nodes online" : "graph offline",
    },
    {
      label: "Routing",
      value: graph?.labels != null ? `${graph.labels}` : "—",
      note: "entity types tracked",
    },
    {
      label: "Tools",
      value: health?.tools?.registered != null ? String(health.tools.registered) : "—",
      note: "connected integrations",
    },
  ];

  return (
    <section className="login-visual">
      <Scene reduced={reduced} />

      <div className="login-brand">
        <span className="brand-mark" aria-hidden="true">
          <Truck size={17} strokeWidth={2} />
        </span>
        <div>
          <h1>LogiPilot AI</h1>
          <span>Logistics control</span>
        </div>
      </div>

      <motion.div
        className="login-copy"
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
      >
        <h2>Every route checked before it leaves the depot.</h2>
        <p>
          Routes come from the road network itself, not from a guess. Active
          incidents, delivery windows and driver hours are enforced as hard
          constraints — an option that breaks one is never recommended.
        </p>
      </motion.div>

      <motion.div
        className="login-metrics"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.12, ease: "easeOut" }}
      >
        {metrics.map((metric) => (
          <div className="login-metric" key={metric.label}>
            <div className="label">{metric.label}</div>
            <div className="value">{metric.value}</div>
            <div className="note">{metric.note}</div>
          </div>
        ))}
      </motion.div>
    </section>
  );
}
