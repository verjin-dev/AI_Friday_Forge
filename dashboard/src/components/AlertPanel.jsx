import { motion } from "framer-motion";
import {
  AlertTriangle,
  CloudRain,
  Info,
  ShieldAlert,
  TrafficCone,
} from "lucide-react";

const SEVERITY = {
  critical: { colour: "var(--rose)", Icon: ShieldAlert, label: "Critical" },
  warning: { colour: "var(--amber)", Icon: AlertTriangle, label: "Warning" },
  info: { colour: "var(--cyan)", Icon: Info, label: "Advisory" },
};

/** Pick an icon that reflects what the alert is actually about. */
function iconFor(alert) {
  const text = `${alert.title} ${alert.detail}`.toLowerCase();
  if (text.includes("rain") || text.includes("flood") || text.includes("water")) {
    return CloudRain;
  }
  if (text.includes("road work") || text.includes("repair") || text.includes("block")) {
    return TrafficCone;
  }
  return SEVERITY[alert.severity]?.Icon || Info;
}

export default function AlertPanel({ alerts, onSelect, loading }) {
  return (
    <section className="card alerts" aria-label="Priority alerts">
      <div className="panel-head">
        <h3>Priority queue</h3>
        <span className="sub">
          {loading ? "checking…" : `${alerts.length} open`}
        </span>
      </div>

      <div className="alert-list">
        {!loading && alerts.length === 0 && (
          <p className="empty-state">
            No alerts. Every lane satisfies its hard constraints.
          </p>
        )}

        {alerts.map((alert, index) => {
          const meta = SEVERITY[alert.severity] || SEVERITY.info;
          const Icon = iconFor(alert);

          return (
            <motion.button
              key={alert.id}
              type="button"
              className="alert"
              onClick={() => onSelect(alert)}
              initial={{ opacity: 0, x: 10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.28, delay: index * 0.05 }}
            >
              <span
                className="alert-icon"
                style={{
                  color: meta.colour,
                  background: `color-mix(in srgb, ${meta.colour} 14%, transparent)`,
                }}
                aria-hidden="true"
              >
                <Icon size={15} strokeWidth={1.9} />
              </span>

              <span style={{ minWidth: 0, flex: 1 }}>
                <span className="alert-title">{alert.title}</span>
                <span className="alert-detail">{alert.detail}</span>
                <span className="alert-meta">
                  <span style={{ color: meta.colour }}>{meta.label}</span>
                  <span>· {alert.route}</span>
                  <span>· {alert.time}</span>
                </span>
              </span>
            </motion.button>
          );
        })}
      </div>
    </section>
  );
}
