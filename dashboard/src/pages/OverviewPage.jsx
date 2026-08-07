import { Info, MapPin, Navigation, RefreshCw } from "lucide-react";

import AlertPanel from "../components/AlertPanel.jsx";
import FleetMap from "../components/FleetMap.jsx";
import MetricCard from "../components/MetricCard.jsx";
import TrackingTable from "../components/TrackingTable.jsx";
import TruckDetailsDrawer from "../components/TruckDetailsDrawer.jsx";
import { STATUSES } from "../data/fleet.js";

export default function OverviewPage({
  fleet,
  metrics,
  rows,
  loading,
  onRefresh,
  notify,
  selectedTruck,
  onSelectTruck,
  routeRequest,
  origin,
  destination,
  onOriginChange,
  onDestinationChange,
  onPlotRoute,
  statusFilter,
  onStatusFilter,
  tableState,
  detailTruck,
  onCloseDetail,
  onBackToFleet,
  onReplan,
}) {
  const alertCount = fleet.alerts.length;

  return (
    <>
      <section className="card welcome">
        <div>
          <h3>
            <span className="live-dot" aria-hidden="true" />
            Live operations
          </h3>
          <p>
            {loading
              ? "Planning lanes against the live road network…"
              : `${fleet.trucks.length} lanes planned · ${alertCount} alert${
                  alertCount === 1 ? "" : "s"
                } · constraint-checked against current incidents`}
          </p>
        </div>
        <div className="welcome-actions">
          <button type="button" className="pill-btn" onClick={onRefresh}>
            <RefreshCw size={14} aria-hidden="true" />
            Refresh
          </button>
          <button type="button" className="pill-btn primary" onClick={onPlotRoute}>
            <Navigation size={14} aria-hidden="true" />
            Plot route
          </button>
        </div>
      </section>

      <section className="metric-grid" aria-label="Key metrics">
        {metrics.map((metric, index) => (
          <MetricCard
            key={metric.key || metric.label}
            metric={metric}
            index={index}
            onSelect={(item) => notify(`${item.label}: ${item.value}`, "info")}
          />
        ))}
      </section>

      <section className="split">
        <div className="card map-card">
          <div className="panel-head">
            <h3>Fleet map</h3>
            <span className="sub">
              {routeRequest
                ? `${routeRequest.origin} → ${routeRequest.destination}`
                : selectedTruck
                ? `${selectedTruck.id} planned route`
                : "no route selected"}
            </span>
          </div>

          <div className="planner">
            <label className="field">
              <MapPin size={14} aria-hidden="true" style={{ color: "var(--indigo)" }} />
              <span className="sr-only">Source</span>
              <input
                value={origin}
                onChange={(event) => onOriginChange(event.target.value)}
                placeholder="Source"
              />
            </label>
            <label className="field">
              <MapPin size={14} aria-hidden="true" style={{ color: "var(--rose)" }} />
              <span className="sr-only">Destination</span>
              <input
                value={destination}
                onChange={(event) => onDestinationChange(event.target.value)}
                placeholder="Destination"
              />
            </label>
            <button type="button" className="pill-btn primary" onClick={onPlotRoute}>
              Plot route
            </button>
          </div>

          <div style={{ position: "relative", flex: 1, display: "flex" }}>
            <FleetMap
              trucks={fleet.trucks}
              selectedTruck={selectedTruck}
              routeRequest={routeRequest}
              onError={(message) => notify(message, "error")}
              onSelectTruck={onSelectTruck}
              onBackToFleet={onBackToFleet}
              showFleetControls
            />
            {detailTruck && (
              <TruckDetailsDrawer
                truck={detailTruck}
                onClose={onCloseDetail}
                onAction={notify}
                onReplan={onReplan}
              />
            )}
          </div>

          {fleet.note && (
            <p className="provenance">
              <Info size={13} aria-hidden="true" style={{ flex: "none", marginTop: 2 }} />
              {fleet.note}
            </p>
          )}
        </div>

        <AlertPanel
          alerts={fleet.alerts}
          loading={loading}
          onSelect={(alert) => {
            const truck = fleet.trucks.find((item) => item.id === alert.truck);
            if (truck) onSelectTruck(truck);
            else notify(alert.detail, "info");
          }}
        />
      </section>

      <div className="chips" role="group" aria-label="Filter by status">
        {STATUSES.map((status) => (
          <button
            key={status}
            type="button"
            className={`chip ${statusFilter === status ? "active" : ""}`}
            onClick={() => onStatusFilter(status)}
            aria-pressed={statusFilter === status}
          >
            {status}
          </button>
        ))}
      </div>

      <TrackingTable
        trucks={rows}
        selectedTruck={selectedTruck}
        onSelectRoute={onSelectTruck}
        loading={loading}
        onAction={(message) => notify(message, "info")}
        {...tableState}
      />
    </>
  );
}
