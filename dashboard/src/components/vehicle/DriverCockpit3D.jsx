import { Canvas, useFrame } from "@react-three/fiber";
import { Float, Line, OrbitControls } from "@react-three/drei";
import {
  AlertTriangle,
  BatteryCharging,
  Clock3,
  Gauge,
  Moon,
  Navigation,
  PackageCheck,
  Radio,
  Route,
  ShieldCheck,
  Sun,
  Truck,
} from "lucide-react";
import { useMemo, useRef } from "react";
import * as THREE from "three";

const SAFE_ROUTE = "#56d8ee";
const RISK_ROUTE = "#f7be69";
const ALERT_ROUTE = "#fb8497";

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function routePointsFromStops(stops = []) {
  const mapped = stops
    .filter((stop) => stop.lat != null && stop.lng != null)
    .map((stop) => ({ name: stop.name, lat: Number(stop.lat), lng: Number(stop.lng) }));

  const points =
    mapped.length >= 2
      ? mapped
      : [
          { name: "Origin", lat: 9.2, lng: 76.2 },
          { name: "Checkpoint", lat: 9.55, lng: 76.65 },
          { name: "Destination", lat: 10.05, lng: 76.45 },
        ];

  const minLat = Math.min(...points.map((point) => point.lat));
  const maxLat = Math.max(...points.map((point) => point.lat));
  const minLng = Math.min(...points.map((point) => point.lng));
  const maxLng = Math.max(...points.map((point) => point.lng));
  const latSpan = Math.max(maxLat - minLat, 0.1);
  const lngSpan = Math.max(maxLng - minLng, 0.1);

  return points.map((point) => [
    ((point.lng - minLng) / lngSpan - 0.5) * 7.2,
    0.18,
    -((point.lat - minLat) / latSpan - 0.5) * 4.8,
  ]);
}

function pointOnPath(points, progress) {
  if (points.length < 2) return new THREE.Vector3(0, 0.25, 0);
  const scaled = clamp(progress, 0, 1) * (points.length - 1);
  const index = Math.min(Math.floor(scaled), points.length - 2);
  const local = scaled - index;
  return new THREE.Vector3(...points[index]).lerp(new THREE.Vector3(...points[index + 1]), local);
}

function CockpitTruck({ points, progress, risk }) {
  const truck = useRef(null);
  const target = useMemo(() => pointOnPath(points, progress), [points, progress]);

  useFrame(({ clock }) => {
    if (!truck.current) return;
    const float = Math.sin(clock.elapsedTime * 2.2) * 0.035;
    truck.current.position.lerp(target, 0.08);
    truck.current.position.y = target.y + 0.32 + float;
    truck.current.rotation.y = -0.32 + Math.sin(clock.elapsedTime * 0.7) * 0.04;
  });

  return (
    <group ref={truck}>
      <mesh castShadow>
        <boxGeometry args={[0.72, 0.34, 0.34]} />
        <meshStandardMaterial color={risk ? RISK_ROUTE : SAFE_ROUTE} metalness={0.5} roughness={0.22} />
      </mesh>
      <mesh position={[-0.38, -0.02, 0]} castShadow>
        <boxGeometry args={[0.18, 0.28, 0.32]} />
        <meshStandardMaterial color="#7f8cff" metalness={0.35} roughness={0.28} />
      </mesh>
      {[-0.25, 0.25].map((x) =>
        [-0.19, 0.19].map((z) => (
          <mesh key={`${x}-${z}`} position={[x, -0.2, z]} rotation={[Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[0.08, 0.08, 0.06, 16]} />
            <meshStandardMaterial color="#071018" />
          </mesh>
        ))
      )}
    </group>
  );
}

function StopMarker({ point, index, active }) {
  return (
    <Float speed={1.4 + index * 0.15} rotationIntensity={0.2} floatIntensity={0.18}>
      <group position={point}>
        <mesh position={[0, 0.24, 0]} castShadow>
          <sphereGeometry args={[active ? 0.16 : 0.11, 20, 20]} />
          <meshStandardMaterial
            color={active ? SAFE_ROUTE : "#8b9cb0"}
            emissive={active ? SAFE_ROUTE : "#263445"}
            emissiveIntensity={active ? 1.2 : 0.45}
          />
        </mesh>
        <mesh position={[0, 0.03, 0]}>
          <cylinderGeometry args={[0.03, 0.08, 0.34, 14]} />
          <meshStandardMaterial color={active ? SAFE_ROUTE : "#526174"} />
        </mesh>
      </group>
    </Float>
  );
}

function DriverScene({ truck }) {
  const points = useMemo(() => routePointsFromStops(truck?.stops), [truck]);
  const progress = clamp((truck?.progress ?? 0) / 100, 0, 1);
  const risk = truck?.delayMinutes > 0 || truck?.feasible === false;
  const completedIndex = Math.floor(progress * Math.max(points.length - 1, 1));

  return (
    <>
      <color attach="background" args={["#09111b"]} />
      <fog attach="fog" args={["#09111b", 7, 16]} />
      <ambientLight intensity={0.72} />
      <directionalLight position={[3.5, 7, 4]} intensity={2.2} color="#dff6ff" castShadow />
      <pointLight position={[-4, 2.5, -2]} intensity={9} color="#7383ff" distance={8} />
      <pointLight position={[4, 2, 1]} intensity={7} color="#54d8ee" distance={7} />

      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[13, 8]} />
        <meshStandardMaterial color="#101c28" roughness={0.78} metalness={0.12} />
      </mesh>

      <gridHelper args={[13, 18, "#213244", "#172434"]} position={[0, 0.02, 0]} />
      <Line points={points} color={risk ? RISK_ROUTE : SAFE_ROUTE} lineWidth={5} transparent opacity={0.95} />
      <Line
        points={points.map(([x, y, z]) => [x, y - 0.05, z])}
        color="#263a52"
        lineWidth={11}
        transparent
        opacity={0.55}
      />

      {points.map((point, index) => (
        <StopMarker key={`${point[0]}-${point[2]}`} point={point} index={index} active={index <= completedIndex} />
      ))}

      {risk && (
        <group position={pointOnPath(points, Math.min(progress + 0.18, 0.92)).toArray()}>
          <mesh position={[0, 0.44, 0]}>
            <sphereGeometry args={[0.2, 24, 24]} />
            <meshStandardMaterial color={ALERT_ROUTE} emissive={ALERT_ROUTE} emissiveIntensity={1.5} />
          </mesh>
          <mesh position={[0, 0.02, 0]} rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[0.34, 0.48, 32]} />
            <meshBasicMaterial color={ALERT_ROUTE} transparent opacity={0.42} />
          </mesh>
        </group>
      )}

      <CockpitTruck points={points} progress={progress} risk={risk} />
      <OrbitControls enablePan={false} enableZoom={false} minPolarAngle={0.74} maxPolarAngle={1.22} autoRotate autoRotateSpeed={0.2} />
    </>
  );
}

function Metric({ icon: Icon, label, value, tone }) {
  return (
    <div className="cockpit-metric">
      <span className="cockpit-metric-icon" style={{ color: tone }}>
        <Icon size={16} aria-hidden="true" />
      </span>
      <span>
        <span className="cockpit-label">{label}</span>
        <strong>{value}</strong>
      </span>
    </div>
  );
}

export default function DriverCockpit3D({
  truck,
  alerts = [],
  loading,
  theme,
  onToggleTheme,
}) {
  const arrival = truck?.etaMinutes
    ? new Date(Date.now() + truck.etaMinutes * 60000).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })
    : truck?.eta;
  const delay = truck?.delayMinutes ? `+${Math.round(truck.delayMinutes)} min` : "on time";
  const risk = truck?.feasible === false || truck?.delayMinutes > 0;
  const compliance = truck?.feasible ? "Compliant route" : truck?.hardViolations?.[0] || "Needs review";
  const recommendation = risk
    ? "Hold current lane and request dispatch confirmation before rerouting."
    : "Continue on the planned route. No critical intervention required.";

  return (
    <section className="card cockpit-card">
      <div className="cockpit-scene">
        {loading ? (
          <div className="cockpit-loading">Preparing cockpit...</div>
        ) : (
          <Canvas shadows dpr={[1, 1.5]} camera={{ position: [0, 5.8, 7.2], fov: 43 }}>
            <DriverScene truck={truck} />
          </Canvas>
        )}
      </div>

      <div className="cockpit-hud">
        <div className="cockpit-title">
          <span className="cockpit-status">
            <Radio size={13} aria-hidden="true" />
            {truck?.liveTraffic ? "Live traffic" : "Graph route"}
          </span>
          <h2>{truck?.id || "Vehicle"} cockpit</h2>
          <p>{truck?.route || "No active assignment"}</p>
        </div>

        <div className="cockpit-actions">
          <button
            type="button"
            className="icon-btn"
            onClick={onToggleTheme}
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          <button type="button" className="icon-btn" title="Navigation active" aria-label="Navigation active">
            <Navigation size={16} />
          </button>
          <button type="button" className="icon-btn" title="Compliance" aria-label="Compliance">
            <ShieldCheck size={16} />
          </button>
        </div>
      </div>

      <div className="cockpit-panel cockpit-panel-left">
        <div>
          <span className="cockpit-label">Next stop</span>
          <strong className="cockpit-large">{truck?.next_stop || "Route complete"}</strong>
          <p>{truck?.distance_remaining_km != null ? `${truck.distance_remaining_km} km remaining` : "Distance not tracked"}</p>
        </div>
        <div className="cockpit-progress">
          <span style={{ width: `${truck?.progress ?? 0}%` }} />
        </div>
        <div className="cockpit-mini-grid">
          <Metric icon={Clock3} label="ETA" value={arrival || "-"} tone="var(--cyan)" />
          <Metric icon={Gauge} label="Delay" value={delay} tone={risk ? "var(--amber)" : "var(--emerald)"} />
          <Metric icon={Route} label="Progress" value={`${truck?.progress ?? 0}%`} tone="var(--indigo)" />
          <Metric icon={PackageCheck} label="Load" value={truck?.load || "-"} tone="var(--amber)" />
        </div>
      </div>

      <div className="cockpit-panel cockpit-panel-right">
        <span className="cockpit-label">AI co-pilot</span>
        <strong>{risk ? "Attention needed" : "Route stable"}</strong>
        <p>{recommendation}</p>
        <div className={`cockpit-verdict ${risk ? "risk" : ""}`}>
          <AlertTriangle size={14} aria-hidden="true" />
          <span>{compliance}</span>
        </div>
        {alerts[0] && <p className="cockpit-note">{alerts[0].title}: {alerts[0].detail}</p>}
      </div>

      <div className="cockpit-strip">
        <Metric icon={Truck} label="Vehicle" value={truck?.vehicle || "unassigned"} tone="var(--cyan)" />
        <Metric icon={BatteryCharging} label="Telemetry" value="not connected" tone="var(--text-faint)" />
        <Metric icon={ShieldCheck} label="Driver" value={truck?.driver || "-"} tone="var(--emerald)" />
      </div>
    </section>
  );
}
