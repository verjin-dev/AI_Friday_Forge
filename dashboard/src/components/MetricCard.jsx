import { motion } from "framer-motion";
import { Activity, AlertTriangle, Gauge, Route, TrendingUp } from "lucide-react";

const ICONS = {
  active_fleet: Activity,
  route_compliance: Gauge,
  open_alerts: AlertTriangle,
  distance_planned: Route,
};

const TONES = {
  emerald: "var(--emerald)",
  indigo: "var(--indigo)",
  rose: "var(--rose)",
  cyan: "var(--cyan)",
  amber: "var(--amber)",
};

export default function MetricCard({ metric, index = 0, onSelect }) {
  const Icon = ICONS[metric.key] || TrendingUp;
  const tone = TONES[metric.tone] || TONES.indigo;

  return (
    <motion.button
      type="button"
      className="card metric"
      onClick={() => onSelect?.(metric)}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, delay: index * 0.06, ease: "easeOut" }}
      whileHover={{ y: -3 }}
      aria-label={`${metric.label}: ${metric.value}. ${metric.note || ""}`}
    >
      <span className="metric-glow" style={{ background: tone }} aria-hidden="true" />

      <span
        className="metric-icon"
        style={{ color: tone, background: `color-mix(in srgb, ${tone} 12%, transparent)` }}
        aria-hidden="true"
      >
        <Icon size={16} strokeWidth={1.9} />
      </span>

      <span className="metric-label">{metric.label}</span>
      <span className="metric-value">{metric.value}</span>

      <span className="metric-foot">
        <span style={{ color: tone, fontWeight: 500 }}>{metric.trend}</span>
        {metric.note && <span className="metric-note">· {metric.note}</span>}
      </span>
    </motion.button>
  );
}
