import { Fragment } from "react";
import { motion } from "framer-motion";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronUp,
  Download,
  Filter,
  Route as RouteIcon,
  Settings2,
  X,
} from "lucide-react";

const PAGE_SIZE = 4;

const STATUS_TONE = {
  "On route": "var(--emerald)",
  Delayed: "var(--amber)",
  "At depot": "var(--rose)",
};

//: Whether the live ETA still meets the delivery commitment.
const SLA_TONE = {
  on_time: "var(--emerald)",
  at_risk: "var(--amber)",
  late: "var(--rose)",
};

const COLUMNS = [
  { key: "id", label: "Truck / driver", sortable: true },
  { key: "route", label: "Route", sortable: true },
  { key: "status", label: "Status", sortable: true },
  { key: "eta", label: "ETA", sortable: true },
  { key: "load", label: "Load", sortable: true },
  { key: "progress", label: "Progress", sortable: true },
];

function SortIcon({ active, direction }) {
  if (!active) return null;
  return direction === "asc" ? <ArrowUp size={11} /> : <ArrowDown size={11} />;
}

export default function TrackingTable({
  trucks,
  selectedTruck,
  onSelectRoute,
  selectedRows,
  onToggleRow,
  onToggleAll,
  onClearSelection,
  expandedId,
  onToggleExpand,
  sort,
  onSort,
  page,
  onPage,
  onAction,
  loading,
}) {
  const totalPages = Math.max(1, Math.ceil(trucks.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const pageRows = trucks.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);
  const allOnPageSelected =
    pageRows.length > 0 && pageRows.every((truck) => selectedRows.includes(truck.id));

  return (
    <section className="card" aria-label="Fleet tracking">
      <div className="panel-head">
        <h3>Live tracking</h3>
        <span className="sub">
          {loading ? "loading…" : `${trucks.length} vehicle${trucks.length === 1 ? "" : "s"}`}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button
            type="button"
            className="icon-btn"
            onClick={() => onAction("Export queued — the CSV will download shortly.")}
            title="Export"
            aria-label="Export fleet data"
          >
            <Download size={15} />
          </button>
          <button
            type="button"
            className="icon-btn"
            onClick={() => onAction("Filters are applied from the status chips above.")}
            title="Filter"
            aria-label="Filter options"
          >
            <Filter size={15} />
          </button>
          <button
            type="button"
            className="icon-btn"
            onClick={() => onAction("Column settings are not configurable in this build.")}
            title="Table settings"
            aria-label="Table settings"
          >
            <Settings2 size={15} />
          </button>
        </div>
      </div>

      {selectedRows.length > 0 && (
        <div className="bulk-bar">
          <strong>{selectedRows.length} selected</strong>
          <button
            type="button"
            className="pill-btn"
            onClick={() =>
              onAction(`Dispatch note sent for ${selectedRows.length} vehicle(s).`)
            }
          >
            Notify drivers
          </button>
          <button
            type="button"
            className="pill-btn"
            onClick={() => onAction(`Re-plan requested for ${selectedRows.length} lane(s).`)}
          >
            Re-plan lanes
          </button>
          <button
            type="button"
            className="pill-btn"
            onClick={onClearSelection}
            style={{ marginLeft: "auto" }}
          >
            <X size={13} aria-hidden="true" /> Clear
          </button>
        </div>
      )}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th style={{ width: 40 }}>
                <input
                  type="checkbox"
                  checked={allOnPageSelected}
                  onChange={() => onToggleAll(pageRows.map((truck) => truck.id))}
                  aria-label="Select all vehicles on this page"
                />
              </th>
              {COLUMNS.map((column) => (
                <th key={column.key}>
                  {column.sortable ? (
                    <button type="button" onClick={() => onSort(column.key)}>
                      {column.label}
                      <SortIcon
                        active={sort.key === column.key}
                        direction={sort.direction}
                      />
                    </button>
                  ) : (
                    column.label
                  )}
                </th>
              ))}
              <th style={{ textAlign: "right" }}>Actions</th>
            </tr>
          </thead>

          <tbody>
            {pageRows.length === 0 && (
              <tr>
                <td colSpan={8}>
                  <p className="empty-state">
                    {loading
                      ? "Loading fleet…"
                      : "No vehicles match the current search and filter."}
                  </p>
                </td>
              </tr>
            )}

            {pageRows.map((truck) => (
              <Fragment key={truck.id}>
                <tr className={truck.id === selectedTruck?.id ? "selected-truck" : ""}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selectedRows.includes(truck.id)}
                      onChange={() => onToggleRow(truck.id)}
                      aria-label={`Select ${truck.id}`}
                    />
                  </td>

                  <td>
                    <div className="cell-primary">{truck.id}</div>
                    <div className="cell-sub">
                      {truck.driver}
                      {truck.vehicle ? ` · ${truck.vehicle}` : ""}
                    </div>
                  </td>

                  <td>
                    <div>{truck.route}</div>
                    {truck.distanceKm != null && (
                      <div className="cell-sub">
                        {truck.distanceKm} km
                        {truck.departure ? ` · dep ${truck.departure}` : ""}
                        {truck.liveTraffic ? " · live traffic" : ""}
                      </div>
                    )}
                  </td>

                  <td>
                    <span
                      className="status-tag"
                      style={{ color: STATUS_TONE[truck.status] || "var(--text-secondary)" }}
                    >
                      <i
                        className="legend-swatch"
                        style={{ background: STATUS_TONE[truck.status] }}
                        aria-hidden="true"
                      />
                      {truck.status}
                    </span>
                  </td>

                  <td>
                    <span style={{ color: SLA_TONE[truck.slaStatus] || undefined }}>
                      {truck.eta}
                    </span>
                    {truck.deliverBy && truck.deliverBy !== "—" && (
                      <div className="cell-sub">
                        by {truck.deliverBy}
                        {truck.etaBufferMinutes > 0 && (
                          <span style={{ color: "var(--amber)" }}>
                            {" "}
                            · +{Math.round(truck.etaBufferMinutes)} min buffer
                          </span>
                        )}
                      </div>
                    )}
                  </td>

                  <td>{truck.load}</td>

                  <td>
                    <div className="progress-track" aria-hidden="true">
                      <div
                        className="progress-fill"
                        style={{ width: `${truck.progress}%` }}
                      />
                    </div>
                    <span className="cell-sub">{truck.progress}%</span>
                  </td>

                  <td>
                    <div className="row-actions">
                      <button
                        type="button"
                        className="row-btn"
                        onClick={() => onSelectRoute(truck)}
                        title={`Show ${truck.id} on the map`}
                        aria-label={`Show ${truck.id} on the map`}
                      >
                        <RouteIcon size={14} />
                      </button>
                      <button
                        type="button"
                        className="row-btn"
                        onClick={() => onToggleExpand(truck.id)}
                        title={expandedId === truck.id ? "Collapse" : "Expand"}
                        aria-label={
                          expandedId === truck.id
                            ? `Collapse ${truck.id}`
                            : `Expand ${truck.id}`
                        }
                        aria-expanded={expandedId === truck.id}
                      >
                        {expandedId === truck.id ? (
                          <ChevronUp size={14} />
                        ) : (
                          <ChevronDown size={14} />
                        )}
                      </button>
                    </div>
                  </td>
                </tr>

                {/* Rendered conditionally rather than through AnimatePresence:
                    under React 19 the exit animation never resolves, which
                    leaves the drawer permanently open once expanded. */}
                {expandedId === truck.id && (
                    <tr>
                      <td colSpan={8} style={{ padding: 0, border: "none" }}>
                        <motion.div
                          className="drawer"
                          initial={{ opacity: 0, y: -6 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ duration: 0.2 }}
                          style={{ overflow: "hidden" }}
                        >
                          <h5>Planned stops</h5>
                          <div className="stop-chain">
                            {truck.stops.length === 0 && (
                              <span className="cell-sub">No mapped stops.</span>
                            )}
                            {truck.stops.map((stop, index) => (
                              <span key={`${stop.name}-${index}`} style={{ display: "contents" }}>
                                <span className="stop-chip">{stop.name}</span>
                                {index < truck.stops.length - 1 && (
                                  <span style={{ color: "var(--text-faint)" }}>→</span>
                                )}
                              </span>
                            ))}
                          </div>

                          {truck.delayFactors?.length > 0 && (
                            <>
                              <h5 style={{ marginTop: 12 }}>Delay factors</h5>
                              <ul
                                style={{
                                  margin: 0,
                                  paddingLeft: 18,
                                  fontSize: 11.5,
                                  color: "var(--text-secondary)",
                                }}
                              >
                                {truck.delayFactors.map((factor, index) => (
                                  <li key={index}>{factor}</li>
                                ))}
                              </ul>
                            </>
                          )}

                          {truck.hardViolations?.map((violation, index) => (
                            <p className="violation" key={index}>
                              Blocked: {violation}
                            </p>
                          ))}
                        </motion.div>
                      </td>
                    </tr>
                  )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      <div className="pagination">
        <span>
          Page {safePage + 1} of {totalPages}
        </span>
        <span className="spacer" />
        <button
          type="button"
          className="pill-btn"
          onClick={() => onPage(Math.max(0, safePage - 1))}
          disabled={safePage === 0}
        >
          Previous
        </button>
        <button
          type="button"
          className="pill-btn"
          onClick={() => onPage(Math.min(totalPages - 1, safePage + 1))}
          disabled={safePage >= totalPages - 1}
        >
          Next
        </button>
      </div>
    </section>
  );
}
