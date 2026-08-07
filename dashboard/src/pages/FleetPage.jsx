import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Check, Minus, Snowflake, Flame, Truck } from "lucide-react";

import PageState from "../components/PageState.jsx";
import { fetchProfiles } from "../data/fleet.js";

function Capability({ on, label, Icon }) {
  return (
    <span
      className="status-tag"
      style={{ color: on ? "var(--emerald)" : "var(--text-faint)" }}
      title={`${label}: ${on ? "yes" : "no"}`}
    >
      <Icon size={11} aria-hidden="true" />
      {label}
    </span>
  );
}

export default function FleetPage({ search, notify }) {
  const [profiles, setProfiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchProfiles(controller.signal)
      .then((payload) => setProfiles(payload.profiles || []))
      .catch((exc) => {
        if (exc.name !== "AbortError") setError(exc.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return profiles;
    return profiles.filter((profile) =>
      [profile.profile_id, profile.licence_held, profile.permitted_zone, profile.sla_promise]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(term))
    );
  }, [profiles, search]);

  const detail = profiles.find((p) => p.profile_id === selected);

  return (
    <>
      <section className="card">
        <div className="panel-head">
          <h3>Vehicle profiles</h3>
          <span className="sub">
            {loading ? "loading…" : `${rows.length} of ${profiles.length}`}
          </span>
          <span className="sub" style={{ marginLeft: "auto" }}>
            from missing_data_template.csv
          </span>
        </div>

        <PageState
          loading={loading}
          error={error}
          empty={rows.length === 0}
          emptyText="No profiles match your search."
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Profile</th>
                  <th>Capacity</th>
                  <th>Driver hours</th>
                  <th>Licence</th>
                  <th>Service window</th>
                  <th>Capability</th>
                  <th style={{ textAlign: "right" }}>Zone</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((profile) => (
                  <tr
                    key={profile.profile_id}
                    onClick={() =>
                      setSelected((current) =>
                        current === profile.profile_id ? null : profile.profile_id
                      )
                    }
                    className={selected === profile.profile_id ? "selected-truck" : ""}
                    style={{ cursor: "pointer" }}
                  >
                    <td>
                      <div className="cell-primary">{profile.profile_id}</div>
                      <div className="cell-sub">{profile.duration_preference} haul</div>
                    </td>
                    <td>
                      <div>{profile.capacity_kg?.toLocaleString()} kg</div>
                      <div className="cell-sub">{profile.capacity_m3} m³</div>
                    </td>
                    <td>
                      <div>{profile.max_daily_driving_hours} h/day</div>
                      <div className="cell-sub">{profile.min_break_minutes} min break</div>
                    </td>
                    <td>
                      <span
                        className="status-tag"
                        style={{
                          color: profile.licence_sufficient
                            ? "var(--emerald)"
                            : "var(--rose)",
                        }}
                      >
                        {profile.licence_sufficient ? (
                          <Check size={11} aria-hidden="true" />
                        ) : (
                          <Minus size={11} aria-hidden="true" />
                        )}
                        {profile.licence_held}
                      </span>
                      <div className="cell-sub">needs {profile.required_licence}</div>
                    </td>
                    <td>
                      <div>{profile.delivery_window}</div>
                      <div className="cell-sub">
                        {profile.sla_promise} · cut-off {profile.warehouse_cutoff}
                      </div>
                    </td>
                    <td>
                      <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                        <Capability
                          on={profile.refrigerated}
                          label="Reefer"
                          Icon={Snowflake}
                        />
                        <Capability
                          on={profile.hazmat_certified}
                          label="Hazmat"
                          Icon={Flame}
                        />
                      </div>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <div>{profile.permitted_zone}</div>
                      <div className="cell-sub">
                        {profile.height_m} m · {profile.axle_count} axles
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </PageState>
      </section>

      {detail && (
        <motion.section
          className="card"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.24 }}
        >
          <div className="panel-head">
            <Truck size={15} aria-hidden="true" style={{ color: "var(--indigo)" }} />
            <h3>{detail.label}</h3>
            <button
              type="button"
              className="pill-btn"
              style={{ marginLeft: "auto" }}
              onClick={() =>
                notify(
                  `Plan a route with ${detail.profile_id} from the Routes page to see its constraints evaluated.`,
                  "info"
                )
              }
            >
              How to use
            </button>
          </div>

          <div style={{ padding: "14px 16px" }}>
            <h5
              style={{
                margin: "0 0 8px",
                fontSize: 11,
                letterSpacing: "0.07em",
                textTransform: "uppercase",
                color: "var(--text-faint)",
              }}
            >
              Still unverifiable with this profile
            </h5>
            <ul
              style={{
                margin: 0,
                paddingLeft: 18,
                fontSize: 12,
                color: "var(--text-secondary)",
                lineHeight: 1.7,
              }}
            >
              {detail.unverifiable_without_more_data.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </div>
        </motion.section>
      )}
    </>
  );
}
