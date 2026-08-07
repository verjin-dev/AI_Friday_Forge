import { useEffect, useMemo, useState } from "react";
import { MapPin, Search as SearchIcon, Truck, TriangleAlert } from "lucide-react";

import PageState from "../components/PageState.jsx";
import { fetchNetwork, fetchProfiles } from "../data/fleet.js";

const GROUPS = [
  { key: "location", label: "Locations", Icon: MapPin, tone: "var(--cyan)" },
  { key: "incident", label: "Incidents", Icon: TriangleAlert, tone: "var(--rose)" },
  { key: "vehicle", label: "Vehicles", Icon: Truck, tone: "var(--indigo)" },
];

export default function SearchPage({ search, onSearch, trucks, onSelectTruck }) {
  const [network, setNetwork] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetchNetwork(controller.signal),
      fetchProfiles(controller.signal),
    ])
      .then(([networkPayload, profilePayload]) => {
        setNetwork(networkPayload);
        setProfiles(profilePayload.profiles || []);
      })
      .catch((exc) => {
        if (exc.name !== "AbortError") setError(exc.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const results = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return { location: [], incident: [], vehicle: [], truck: [] };

    const match = (value) => String(value ?? "").toLowerCase().includes(term);

    return {
      location: (network?.locations || [])
        .filter((item) => match(item.name) || match(item.type))
        .slice(0, 12),
      incident: (network?.incidents || [])
        .filter(
          (item) =>
            match(item.incident_id) ||
            match(item.type) ||
            match(item.location) ||
            match(item.severity)
        )
        .slice(0, 12),
      vehicle: profiles
        .filter(
          (item) =>
            match(item.profile_id) ||
            match(item.licence_held) ||
            match(item.permitted_zone)
        )
        .slice(0, 12),
      truck: trucks
        .filter((item) => match(item.id) || match(item.route) || match(item.load))
        .slice(0, 12),
    };
  }, [search, network, profiles, trucks]);

  const total =
    results.location.length +
    results.incident.length +
    results.vehicle.length +
    results.truck.length;

  return (
    <PageState loading={loading} error={error}>
      <section className="card">
        <div className="panel-head">
          <SearchIcon size={15} aria-hidden="true" style={{ color: "var(--cyan)" }} />
          <h3>Search</h3>
          <span className="sub">
            {search.trim() ? `${total} result${total === 1 ? "" : "s"}` : "everything"}
          </span>
        </div>

        <div className="planner">
          <label className="field" style={{ flex: "1 1 100%" }}>
            <SearchIcon size={14} aria-hidden="true" style={{ color: "var(--text-faint)" }} />
            <span className="sr-only">Search locations, incidents and vehicles</span>
            <input
              value={search}
              onChange={(event) => onSearch(event.target.value)}
              placeholder="Search locations, incidents, vehicles, lanes…"
              autoFocus
            />
          </label>
        </div>

        {!search.trim() && (
          <p className="empty-state">
            Type to search {network?.locations?.length ?? 0} locations,{" "}
            {network?.incidents?.length ?? 0} incidents and {profiles.length}{" "}
            vehicle profiles.
          </p>
        )}

        {search.trim() && total === 0 && (
          <p className="empty-state">Nothing matches “{search}”.</p>
        )}
      </section>

      {results.truck.length > 0 && (
        <section className="card">
          <div className="panel-head">
            <Truck size={15} aria-hidden="true" style={{ color: "var(--emerald)" }} />
            <h3>Lanes</h3>
            <span className="sub">{results.truck.length}</span>
          </div>
          <div className="alert-list">
            {results.truck.map((truck) => (
              <button
                key={truck.id}
                type="button"
                className="alert"
                onClick={() => onSelectTruck(truck)}
              >
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span className="alert-title">
                    {truck.id} · {truck.route}
                  </span>
                  <span className="alert-detail">
                    {truck.status} · {truck.load}
                    {truck.distanceKm ? ` · ${truck.distanceKm} km` : ""}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {GROUPS.map(({ key, label, Icon, tone }) =>
        results[key].length > 0 ? (
          <section className="card" key={key}>
            <div className="panel-head">
              <Icon size={15} aria-hidden="true" style={{ color: tone }} />
              <h3>{label}</h3>
              <span className="sub">{results[key].length}</span>
            </div>
            <div className="table-wrap">
              <table>
                <tbody>
                  {key === "location" &&
                    results.location.map((item) => (
                      <tr key={item.name}>
                        <td className="cell-primary">{item.name}</td>
                        <td>{item.type}</td>
                        <td className="cell-sub">
                          {item.has_coordinates
                            ? `${item.latitude?.toFixed(3)}, ${item.longitude?.toFixed(3)}`
                            : "no coordinates"}
                        </td>
                        <td className="cell-sub">
                          {item.incidents?.length
                            ? `${item.incidents.length} active incident(s)`
                            : "clear"}
                        </td>
                      </tr>
                    ))}

                  {key === "incident" &&
                    results.incident.map((item) => (
                      <tr key={item.incident_id}>
                        <td className="cell-primary">{item.incident_id}</td>
                        <td>{item.type}</td>
                        <td>{item.severity}</td>
                        <td>{item.location}</td>
                        <td
                          className="cell-sub"
                          style={{
                            color: item.is_blocking ? "var(--rose)" : undefined,
                          }}
                        >
                          {item.status}
                          {item.is_blocking ? " · blocking" : ""}
                        </td>
                      </tr>
                    ))}

                  {key === "vehicle" &&
                    results.vehicle.map((item) => (
                      <tr key={item.profile_id}>
                        <td className="cell-primary">{item.profile_id}</td>
                        <td>{item.capacity_kg?.toLocaleString()} kg</td>
                        <td>{item.licence_held}</td>
                        <td>{item.permitted_zone}</td>
                        <td className="cell-sub">{item.sla_promise}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null
      )}
    </PageState>
  );
}
