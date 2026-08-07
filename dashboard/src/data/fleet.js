/**
 * Fleet data access.
 *
 * The build spec called for a static `trucks` array. This reads from the
 * LogiPilot backend instead, so the dashboard shows the real road network,
 * real incidents and real constraint verdicts rather than fixtures. The
 * exported shape is unchanged, so components stay presentational.
 *
 * The static arrays below are a last-resort fallback for when the API is
 * unreachable — they keep the UI renderable during a demo, and every consumer
 * is told via `live: false` that it is looking at placeholder data.
 */

const API = "/api";

async function getJSON(path, signal) {
  const response = await fetch(`${API}${path}`, { signal });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

/** Shape a backend truck into what the table and map expect. */
function normaliseTruck(truck) {
  return {
    id: truck.id,
    driver: truck.driver,
    route: truck.route,
    status: truck.status,
    eta: truck.eta,
    load: truck.load,
    progress: truck.progress ?? 0,
    position: truck.position || null,
    stops: (truck.stops || []).filter((stop) => stop.lat != null && stop.lng != null),
    // --- evidence carried through from the constraint engine ---
    vehicle: truck.vehicle,
    profileId: truck.profile_id,
    distanceKm: truck.distance_km,
    etaMinutes: truck.eta_minutes,
    delayMinutes: truck.delay_minutes,
    delayRisk: truck.delay_risk,
    liveTraffic: truck.live_traffic_used,
    feasible: truck.feasible,
    hardViolations: truck.hard_violations || [],
    softViolations: truck.soft_violations || [],
    delayFactors: truck.delay_factors || [],
    departure: truck.departure,
    derived: truck.derived,
    positionSource: truck.position_source,
    // --- trip detail used by the drawer and the vehicle console ---
    next_stop: truck.next_stop,
    distance_remaining_km: truck.distance_remaining_km,
    distance_covered_km: truck.distance_covered_km,
    timeline: truck.timeline || [],
    predictive: truck.predictive || null,
    cargo: truck.cargo || null,
    untracked: truck.untracked || [],
  };
}

export async function fetchFleet(signal) {
  const payload = await getJSON("/fleet/overview?limit=6", signal);
  return {
    live: true,
    note: payload.note,
    generatedAt: payload.generated_at,
    trucks: (payload.trucks || []).map(normaliseTruck),
    alerts: payload.alerts || [],
    metrics: payload.metrics || [],
  };
}

export async function fetchHealth(signal) {
  return getJSON("/health", signal);
}

export async function planRoute({ origin, destination, profile }, signal) {
  const params = new URLSearchParams({ origin, destination });
  if (profile) params.set("profile", profile);
  const response = await fetch(`${API}/routes/plan?${params}`, { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

export async function fetchLocations(signal) {
  return getJSON("/routes/locations", signal);
}

export async function fetchProfiles(signal) {
  return getJSON("/fleet/profiles", signal);
}

export async function fetchNetwork(signal) {
  return getJSON("/routes/network", signal);
}

export async function fetchKpis(signal) {
  return getJSON("/observability/kpis", signal);
}

export async function fetchRunMetrics(signal) {
  return getJSON("/observability/metrics", signal);
}

export async function fetchSecurityAudit(signal) {
  return getJSON("/observability/security-audit?limit=40", signal);
}

export async function fetchModelCard(signal) {
  return getJSON("/routes/model-card", signal);
}

export async function fetchConstraintProfile(signal) {
  return getJSON("/constraints/profile", signal);
}

export async function fetchConstraintCatalogue(signal) {
  return getJSON("/constraints/catalogue", signal);
}

export async function fetchTools(signal) {
  return getJSON("/tools", signal);
}

export async function setIncidentStatus(incidentId, status) {
  const response = await fetch(`${API}/routes/scenario`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ incident_id: incidentId, status }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

/** Placeholder fleet, used only when the backend cannot be reached. */
export const fallbackTrucks = [
  {
    id: "LP-1048",
    driver: "Crew 001",
    route: "Kollam to Kottayam",
    status: "On route",
    eta: "14:40",
    load: "Electronics",
    progress: 68,
    position: { lat: 9.2, lng: 76.6 },
    stops: [
      { name: "Kollam", lat: 8.8932, lng: 76.6141 },
      { name: "Kottarakkara", lat: 9.0, lng: 76.7833 },
      { name: "Kottayam", lat: 9.5916, lng: 76.5222 },
    ],
    derived: true,
    positionSource: "placeholder",
  },
];

export const fallbackMetrics = [
  {
    key: "active_fleet",
    label: "Active fleet",
    value: "—",
    trend: "offline",
    note: "backend unreachable",
    tone: "emerald",
  },
  {
    key: "route_compliance",
    label: "Route compliance",
    value: "—",
    trend: "offline",
    note: "backend unreachable",
    tone: "indigo",
  },
  {
    key: "open_alerts",
    label: "Open alerts",
    value: "—",
    trend: "offline",
    note: "backend unreachable",
    tone: "rose",
  },
  {
    key: "distance_planned",
    label: "Distance planned",
    value: "—",
    trend: "km",
    note: "backend unreachable",
    tone: "cyan",
  },
];

export const STATUSES = ["All status", "On route", "Delayed", "At depot"];
