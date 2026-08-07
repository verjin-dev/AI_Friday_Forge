import { useMemo, useState } from "react";

/**
 * Geographic map of the road network.
 *
 * Drawn as inline SVG from real lat/lon rather than tiles: it works offline,
 * needs no map key, and keeps every pin traceable to a graph node.
 */

const WIDTH = 620;
const HEIGHT = 460;
const PAD = 46;

function project(locations) {
  const points = locations.filter((item) => item.has_coordinates);
  if (!points.length) return null;

  const lats = points.map((item) => item.latitude);
  const lons = points.map((item) => item.longitude);
  let [minLat, maxLat] = [Math.min(...lats), Math.max(...lats)];
  let [minLon, maxLon] = [Math.min(...lons), Math.max(...lons)];

  // Avoid a degenerate box when everything sits on one line.
  if (maxLat - minLat < 0.05) {
    minLat -= 0.05;
    maxLat += 0.05;
  }
  if (maxLon - minLon < 0.05) {
    minLon -= 0.05;
    maxLon += 0.05;
  }

  return (lat, lon) => ({
    x: PAD + ((lon - minLon) / (maxLon - minLon)) * (WIDTH - PAD * 2),
    y: PAD + ((maxLat - lat) / (maxLat - minLat)) * (HEIGHT - PAD * 2),
  });
}

function severityColour(severity) {
  const value = (severity || "").toLowerCase();
  if (value === "critical") return "#f85149";
  if (value === "high") return "#ff8c42";
  if (value === "medium") return "#d29922";
  return "#8b9cb0";
}

export default function MapView({ network, plan, selectedRoute }) {
  const [hover, setHover] = useState(null);

  const locations = network?.locations || [];
  const projectPoint = useMemo(() => project(locations), [locations]);

  const byName = useMemo(() => {
    const map = new Map();
    locations.forEach((item) => map.set(item.name, item));
    return map;
  }, [locations]);

  const incidentsByLocation = useMemo(() => {
    const map = new Map();
    (network?.incidents || [])
      .filter((item) => item.is_active)
      .forEach((item) => {
        const list = map.get(item.location) || [];
        list.push(item);
        map.set(item.location, list);
      });
    return map;
  }, [network]);

  if (!projectPoint) {
    return (
      <div className="empty">
        No coordinates available for the network locations yet.
      </div>
    );
  }

  const pointFor = (name) => {
    const item = byName.get(name);
    if (!item || !item.has_coordinates) return null;
    return projectPoint(item.latitude, item.longitude);
  };

  const routeStops = selectedRoute?.stops || [];
  const routeEdges = [];
  for (let index = 0; index < routeStops.length - 1; index += 1) {
    routeEdges.push(`${routeStops[index]}|${routeStops[index + 1]}`);
  }
  const onRoute = new Set(routeStops);

  const isRouteEdge = (from, to) =>
    routeEdges.includes(`${from}|${to}`) || routeEdges.includes(`${to}|${from}`);

  return (
    <div className="graph-wrap">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%" role="img">
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="5"
            markerHeight="5"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#4aa8ff" />
          </marker>
        </defs>

        {/* road edges */}
        {(network?.edges || []).map((edge, index) => {
          const from = pointFor(edge.from);
          const to = pointFor(edge.to);
          if (!from || !to) return null;

          const highlighted = isRouteEdge(edge.from, edge.to);
          const alternate = edge.kind === "alternate";

          return (
            <line
              key={`edge-${index}`}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              stroke={highlighted ? "#4aa8ff" : alternate ? "#4b5b6e" : "#2b3a4b"}
              strokeWidth={highlighted ? 4 : 2}
              strokeDasharray={alternate ? "7 5" : undefined}
              opacity={highlighted ? 1 : 0.75}
            />
          );
        })}

        {/* edge labels for the selected route */}
        {(network?.edges || []).map((edge, index) => {
          if (!isRouteEdge(edge.from, edge.to)) return null;
          const from = pointFor(edge.from);
          const to = pointFor(edge.to);
          if (!from || !to) return null;
          return (
            <text
              key={`label-${index}`}
              x={(from.x + to.x) / 2}
              y={(from.y + to.y) / 2 - 6}
              fill="#8b9cb0"
              fontSize="10"
              textAnchor="middle"
            >
              {edge.road_name || edge.via} · {edge.distance_km} km
            </text>
          );
        })}

        {/* locations */}
        {locations.map((item) => {
          if (!item.has_coordinates) return null;
          const point = projectPoint(item.latitude, item.longitude);
          const incidents = incidentsByLocation.get(item.name) || [];
          const worst = incidents.reduce((acc, incident) => {
            const order = ["low", "medium", "high", "critical"];
            return order.indexOf((incident.severity || "").toLowerCase()) >
              order.indexOf(acc)
              ? incident.severity.toLowerCase()
              : acc;
          }, "");
          const highlighted = onRoute.has(item.name);

          return (
            <g
              key={item.name}
              onMouseEnter={() => setHover({ ...item, incidents, ...point })}
              onMouseLeave={() => setHover(null)}
              style={{ cursor: "pointer" }}
            >
              {incidents.length > 0 && (
                <circle
                  cx={point.x}
                  cy={point.y}
                  r={16}
                  fill={severityColour(worst)}
                  opacity="0.18"
                />
              )}
              <circle
                cx={point.x}
                cy={point.y}
                r={highlighted ? 8 : 6}
                fill={incidents.length ? severityColour(worst) : "#4aa8ff"}
                stroke={highlighted ? "#e6edf3" : "#0a0e13"}
                strokeWidth="2"
              />
              <text
                x={point.x + 11}
                y={point.y + 4}
                fill={highlighted ? "#e6edf3" : "#8b9cb0"}
                fontSize="11"
                fontWeight={highlighted ? 600 : 400}
              >
                {item.name}
              </text>
            </g>
          );
        })}

        {/* origin / destination markers */}
        {plan &&
          [plan.origin, plan.destination].map((name, index) => {
            const point = pointFor(name);
            if (!point) return null;
            return (
              <text
                key={`marker-${name}`}
                x={point.x}
                y={point.y - 14}
                fill="#3fb950"
                fontSize="11"
                fontWeight="700"
                textAnchor="middle"
              >
                {index === 0 ? "START" : "END"}
              </text>
            );
          })}

        {hover && (
          <g pointerEvents="none">
            <rect
              x={Math.min(hover.x + 12, WIDTH - 190)}
              y={Math.max(hover.y - 44, 6)}
              width="180"
              height={hover.incidents.length ? 54 : 34}
              rx="6"
              fill="#121821"
              stroke="#22303f"
            />
            <text
              x={Math.min(hover.x + 20, WIDTH - 182)}
              y={Math.max(hover.y - 26, 24)}
              fill="#e6edf3"
              fontSize="11"
              fontWeight="600"
            >
              {hover.name} · {hover.type || "Location"}
            </text>
            {hover.incidents.slice(0, 2).map((incident, index) => (
              <text
                key={incident.incident_id}
                x={Math.min(hover.x + 20, WIDTH - 182)}
                y={Math.max(hover.y - 12 + index * 13, 38 + index * 13)}
                fill={severityColour(incident.severity)}
                fontSize="10"
              >
                {incident.severity} {incident.type}
              </text>
            ))}
          </g>
        )}
      </svg>

      <div className="legend">
        <span>
          <i className="swatch" style={{ background: "#4aa8ff" }} /> clear
        </span>
        <span>
          <i className="swatch" style={{ background: "#d29922" }} /> medium
        </span>
        <span>
          <i className="swatch" style={{ background: "#ff8c42" }} /> high
        </span>
        <span>
          <i className="swatch" style={{ background: "#f85149" }} /> critical
        </span>
        <span>— road</span>
        <span>-- alternate</span>
      </div>
    </div>
  );
}
