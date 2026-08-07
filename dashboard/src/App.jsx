import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";

import Header from "./components/Header.jsx";
import Sidebar from "./components/Sidebar.jsx";
import Toast from "./components/Toast.jsx";

import AnalyticsPage from "./pages/AnalyticsPage.jsx";
import FleetPage from "./pages/FleetPage.jsx";
import NotificationsPage from "./pages/NotificationsPage.jsx";
import OverviewPage from "./pages/OverviewPage.jsx";
import RoutesPage from "./pages/RoutesPage.jsx";
import SearchPage from "./pages/SearchPage.jsx";
import SettingsPage from "./pages/SettingsPage.jsx";

import { fallbackMetrics, fallbackTrucks, fetchFleet, replanRoute } from "./data/fleet.js";

const TOAST_MS = 3600;

export default function App({ session, onSignOut, onNavigate }) {
  // --- dashboard state ---
  const [statusFilter, setStatusFilter] = useState("All status");
  const [search, setSearch] = useState("");
  const [activeNav, setActiveNav] = useState("Overview");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [selectedTruck, setSelectedTruck] = useState(null);
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [routeRequest, setRouteRequest] = useState(null);
  const [theme, setTheme] = useState("dark");
  const [selectedRows, setSelectedRows] = useState([]);
  const [expandedId, setExpandedId] = useState(null);
  const [sort, setSort] = useState({ key: "id", direction: "asc" });
  const [page, setPage] = useState(0);
  const [toast, setToast] = useState(null);
  const [detailTruck, setDetailTruck] = useState(null);

  // --- live data ---
  const [fleet, setFleet] = useState({
    trucks: [],
    alerts: [],
    metrics: [],
    live: false,
    note: "",
  });
  const [loading, setLoading] = useState(true);
  const toastTimer = useRef(null);

  const notify = useCallback((message, kind = "info") => {
    setToast({ message, kind, id: Date.now() });
  }, []);

  useEffect(() => {
    if (!toast) return undefined;
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(toastTimer.current);
  }, [toast]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const load = useCallback(
    async (announce = false) => {
      setLoading(true);
      try {
        const payload = await fetchFleet();
        setFleet(payload);
        setSelectedTruck((current) => {
          if (!current) return payload.trucks[0] || null;
          return payload.trucks.find((t) => t.id === current.id) || payload.trucks[0];
        });
        if (announce) notify("Fleet refreshed from the live network.", "success");
      } catch (exc) {
        setFleet({
          trucks: fallbackTrucks,
          alerts: [],
          metrics: fallbackMetrics,
          live: false,
          note: `Backend unreachable (${exc.message}). Showing placeholder data.`,
        });
        notify(`Could not reach the platform: ${exc.message}`, "error");
      } finally {
        setLoading(false);
      }
    },
    [notify]
  );

  useEffect(() => {
    load();
  }, [load]);

  // --- derived rows ---
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    const filtered = fleet.trucks.filter((truck) => {
      const matchesStatus =
        statusFilter === "All status" || truck.status === statusFilter;
      if (!matchesStatus) return false;
      if (!term) return true;
      return [truck.id, truck.driver, truck.route, truck.load, truck.vehicle]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(term));
    });

    const { key, direction } = sort;
    const factor = direction === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const left = a[key];
      const right = b[key];
      if (typeof left === "number" && typeof right === "number") {
        return (left - right) * factor;
      }
      return String(left).localeCompare(String(right)) * factor;
    });
  }, [fleet.trucks, search, statusFilter, sort]);

  // --- handlers ---
  const handleSearch = useCallback((value) => {
    setSearch(value);
    setPage(0);
  }, []);

  const handleStatusFilter = useCallback((value) => {
    setStatusFilter(value);
    setPage(0);
  }, []);

  const handleSort = useCallback((key) => {
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" }
    );
  }, []);

  const showTruckRoute = useCallback(
    (truck) => {
      setSelectedTruck(truck);
      const stops = truck.stops || [];
      if (stops.length >= 2) {
        setOrigin(stops[0].name);
        setDestination(stops[stops.length - 1].name);
      }
      setRouteRequest(null);
      setDetailTruck(truck);
      setActiveNav("Overview");
      notify(`Mapped ${truck.id} · ${truck.route}`, "success");
    },
    [notify]
  );

  const backToFleet = useCallback(() => {
    setSelectedTruck(null);
    setDetailTruck(null);
    setRouteRequest(null);
    notify("Returned to the full fleet view.", "info");
  }, [notify]);

  const plotRoute = useCallback(() => {
    if (!origin.trim() || !destination.trim()) {
      notify("Enter both a source and a destination.", "error");
      return;
    }
    setRouteRequest({ origin: origin.trim(), destination: destination.trim() });
    notify(`Requesting directions: ${origin} → ${destination}`, "info");
  }, [origin, destination, notify]);

  const handleReplan = useCallback(
    async (truck) => {
      if (!truck) return;
      const rawStops =
        truck.stops && truck.stops.length > 0
          ? truck.stops.map((s) => (typeof s === "string" ? s : s.name))
          : truck.route
          ? truck.route.split(" to ")
          : ["Kochi", "Thiruvananthapuram"];

      const currentNode =
        truck.next_stop || (rawStops.length > 1 ? rawStops[1] : rawStops[0]);
      const currentIndex = rawStops.indexOf(currentNode);
      const nextIndex =
        currentIndex >= 0 && currentIndex < rawStops.length - 1
          ? currentIndex + 1
          : 1;
      const blockedEdge = [
        rawStops[Math.max(0, nextIndex - 1)],
        rawStops[nextIndex],
      ];

      notify(`Re-routing ${truck.id} via Segment Replanner…`, "info");

      try {
        const outcome = await replanRoute({
          stops: rawStops,
          current_node: currentNode,
          blocked_edge: blockedEdge,
          reason: "Dispatcher manual re-route request",
        });

        if (outcome && outcome.route && outcome.route.stops) {
          const coords = outcome.coordinates || {};
          const newStops = outcome.route.stops.map((name) => {
            const c = coords[name];
            return {
              name,
              lat: c?.latitude ?? null,
              lng: c?.longitude ?? null,
            };
          });
          const newRouteStr = `${newStops[0].name} to ${newStops[newStops.length - 1].name}`;

          setFleet((prev) => ({
            ...prev,
            trucks: prev.trucks.map((t) => {
              if (t.id === truck.id) {
                return {
                  ...t,
                  stops: newStops,
                  route: newRouteStr,
                  distanceKm: (t.distanceKm || 0) + (outcome.added_distance_km || 0),
                  status: "On route",
                  feasible: true,
                  softViolations: [
                    `Re-routed via segment replanner (+${
                      outcome.added_distance_km || 0
                    } km)`,
                  ],
                };
              }
              return t;
            }),
          }));

          const updatedTruck = {
            ...truck,
            stops: newStops,
            route: newRouteStr,
            distanceKm: (truck.distanceKm || 0) + (outcome.added_distance_km || 0),
            status: "On route",
            feasible: true,
          };

          setSelectedTruck(updatedTruck);
          setDetailTruck(updatedTruck);

          notify(
            `Re-routed ${truck.id}! Spliced segment: ${newStops.join(" → ")}`,
            "success"
          );
        } else {
          notify(
            `Re-planning result: ${
              outcome?.note || outcome?.reason || "No alternate path found"
            }`,
            "warning"
          );
        }
      } catch (err) {
        notify(`Re-routing failed: ${err.message}`, "error");
      }
    },
    [notify]
  );

  const toggleRow = useCallback((id) => {
    setSelectedRows((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id]
    );
  }, []);

  const toggleAll = useCallback((ids) => {
    setSelectedRows((current) => {
      const allSelected = ids.every((id) => current.includes(id));
      return allSelected
        ? current.filter((id) => !ids.includes(id))
        : [...new Set([...current, ...ids])];
    });
  }, []);

  const metrics = fleet.metrics.length ? fleet.metrics : fallbackMetrics;
  const alertCount = fleet.alerts.length;

  const tableState = {
    selectedRows,
    onToggleRow: toggleRow,
    onToggleAll: toggleAll,
    onClearSelection: () => setSelectedRows([]),
    expandedId,
    onToggleExpand: (id) =>
      setExpandedId((current) => (current === id ? null : id)),
    sort,
    onSort: handleSort,
    page,
    onPage: setPage,
  };

  const renderPage = () => {
    switch (activeNav) {
      case "Fleet":
        return <FleetPage search={search} notify={notify} />;
      case "Routes":
        return <RoutesPage notify={notify} />;
      case "Analytics":
        return <AnalyticsPage />;
      case "Search":
        return (
          <SearchPage
            search={search}
            onSearch={handleSearch}
            trucks={fleet.trucks}
            onSelectTruck={showTruckRoute}
          />
        );
      case "Notifications":
        return (
          <NotificationsPage
            search={search}
            notify={notify}
            onFleetChanged={() => load()}
          />
        );
      case "Settings":
        return (
          <SettingsPage
            theme={theme}
            onToggleTheme={() =>
              setTheme((value) => (value === "dark" ? "light" : "dark"))
            }
          />
        );
      default:
        return (
          <OverviewPage
            fleet={fleet}
            metrics={metrics}
            rows={rows}
            loading={loading}
            onRefresh={() => load(true)}
            notify={notify}
            selectedTruck={selectedTruck}
            onSelectTruck={showTruckRoute}
            routeRequest={routeRequest}
            origin={origin}
            destination={destination}
            onOriginChange={setOrigin}
            onDestinationChange={setDestination}
            onPlotRoute={plotRoute}
            statusFilter={statusFilter}
            onStatusFilter={handleStatusFilter}
            tableState={tableState}
            detailTruck={detailTruck}
            onCloseDetail={() => setDetailTruck(null)}
            onBackToFleet={backToFleet}
            onReplan={handleReplan}
          />
        );
    }
  };

  return (
    <div className="shell" data-collapsed={sidebarCollapsed}>
      <Sidebar
        activeNav={activeNav}
        onNavigate={setActiveNav}
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((value) => !value)}
        alertCount={alertCount}
        session={session}
        onSignOut={onSignOut}
        onOpenVehicle={() => onNavigate?.("/vehicle")}
      />

      <main className="workspace">
        <div className="stack">
          <Header
            title={activeNav}
            search={search}
            onSearch={handleSearch}
            theme={theme}
            onToggleTheme={() =>
              setTheme((value) => (value === "dark" ? "light" : "dark"))
            }
            onAskAI={() =>
              notify(
                "Ask AI runs the 12-agent workflow — open the LogiPilot agent console for the full trace.",
                "info"
              )
            }
            onQuickRoute={() => setActiveNav("Routes")}
            alertCount={alertCount}
            session={session}
            onSignOut={onSignOut}
          />

          {/* Keyed on the page so each one animates in on mount. Deliberately
              not wrapped in AnimatePresence mode="wait": under React 19 the
              exit never resolves, which leaves the previous page mounted and
              the new one never rendered. */}
          <motion.div
            key={activeNav}
            className="stack"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            {renderPage()}
          </motion.div>
        </div>
      </main>

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}
