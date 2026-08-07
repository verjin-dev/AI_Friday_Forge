import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MarkerClusterer } from "@googlemaps/markerclusterer";
import {
  ArrowLeft,
  Crosshair,
  Layers,
  Minus,
  Navigation,
  Plus,
  Search,
} from "lucide-react";

const API_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY;
const KERALA_CENTRE = { lat: 10.4, lng: 76.3 };

const SELECTED_COLOUR = "#56d8ee";
const ALTERNATE_COLOUR = "#5b7186";
const STOP_COLOUR = "#f7be69";

/** Vehicle marker colour by operational status. */
const STATUS_COLOUR = {
  "On route": "#7886ff",
  Delayed: "#fb8497",
  "At depot": "#8b9cb0",
};

const FILTERS = [
  { key: "all", label: "All vehicles" },
  { key: "On route", label: "On route" },
  { key: "Delayed", label: "Delayed" },
  { key: "At depot", label: "At depot" },
  { key: "risk", label: "High risk" },
  { key: "blocked", label: "No compliant route" },
];

const DARK_STYLE = [
  { elementType: "geometry", stylers: [{ color: "#0f1620" }] },
  { elementType: "labels.text.stroke", stylers: [{ color: "#0f1620" }] },
  { elementType: "labels.text.fill", stylers: [{ color: "#7d8fa0" }] },
  {
    featureType: "administrative",
    elementType: "geometry",
    stylers: [{ color: "#2a3746" }],
  },
  { featureType: "poi", stylers: [{ visibility: "off" }] },
  {
    featureType: "road",
    elementType: "geometry",
    stylers: [{ color: "#1c2735" }],
  },
  {
    featureType: "road.highway",
    elementType: "geometry",
    stylers: [{ color: "#27374a" }],
  },
  { featureType: "transit", stylers: [{ visibility: "off" }] },
  { featureType: "water", elementType: "geometry", stylers: [{ color: "#0a1018" }] },
];

/** Load the Maps JS API exactly once per page. */
let loaderPromise = null;
function loadGoogleMaps() {
  if (window.google?.maps) return Promise.resolve(window.google);
  if (loaderPromise) return loaderPromise;

  if (!API_KEY) {
    return Promise.reject(
      new Error(
        "VITE_GOOGLE_MAPS_API_KEY is not set. Copy .env.example to .env.local and add a browser key."
      )
    );
  }

  loaderPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://maps.googleapis.com/maps/api/js?key=${API_KEY}&v=weekly`;
    script.async = true;
    script.defer = true;
    script.onload = () =>
      window.google?.maps
        ? resolve(window.google)
        : reject(new Error("Maps script loaded but google.maps is unavailable."));
    script.onerror = () =>
      reject(
        new Error(
          "Could not load Google Maps. Check the key, its referrer restrictions, and that the Maps JavaScript API is enabled."
        )
      );
    document.head.appendChild(script);
  });

  return loaderPromise;
}

function pinIcon(colour, label) {
  return {
    path: window.google.maps.SymbolPath.CIRCLE,
    scale: label ? 9 : 6,
    fillColor: colour,
    fillOpacity: 1,
    strokeColor: "#0b1018",
    strokeWeight: 2,
  };
}

export default function FleetMap({
  trucks,
  selectedTruck,
  routeRequest,
  onStatus,
  onError,
  onSelectTruck,
  onBackToFleet,
  showFleetControls = false,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const overlaysRef = useRef([]);
  const fleetMarkersRef = useRef([]);
  const clustererRef = useRef(null);
  const directionsRef = useRef(null);
  const trafficRef = useRef(null);

  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState(null);
  const [status, setStatus] = useState("Loading map…");
  const [suggestions, setSuggestions] = useState([]);
  const [chosen, setChosen] = useState(0);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");

  const visibleTrucks = useMemo(() => {
    const term = query.trim().toLowerCase();
    return trucks.filter((truck) => {
      const matchesFilter =
        filter === "all"
          ? true
          : filter === "risk"
          ? ["high", "severe"].includes(truck.delayRisk)
          : filter === "blocked"
          ? truck.feasible === false
          : truck.status === filter;
      if (!matchesFilter) return false;
      if (!term) return true;
      return [truck.id, truck.driver, truck.route, truck.load, truck.vehicle]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(term));
    });
  }, [trucks, filter, query]);

  const summary = useMemo(
    () => ({
      active: trucks.filter((t) => t.status === "On route").length,
      delayed: trucks.filter((t) => t.status === "Delayed").length,
      depot: trucks.filter((t) => t.status === "At depot").length,
      critical: trucks.filter((t) => t.feasible === false).length,
    }),
    [trucks]
  );

  const report = useCallback(
    (message) => {
      setStatus(message);
      onStatus?.(message);
    },
    [onStatus]
  );

  const clearOverlays = useCallback(() => {
    overlaysRef.current.forEach((item) => item.setMap(null));
    overlaysRef.current = [];
  }, []);

  // --- initialise ---------------------------------------------------------
  const lastFittedKeyRef = useRef(null);

  useEffect(() => {
    if (mapRef.current) return;
    let cancelled = false;

    loadGoogleMaps()
      .then((google) => {
        if (cancelled || !containerRef.current || mapRef.current) return;

        mapRef.current = new google.maps.Map(containerRef.current, {
          center: KERALA_CENTRE,
          zoom: 7,
          styles: DARK_STYLE,
          disableDefaultUI: true,
          gestureHandling: "greedy",
        });
        directionsRef.current = new google.maps.DirectionsService();

        // Live traffic is the point of a control-room map.
        trafficRef.current = new google.maps.TrafficLayer();
        trafficRef.current.setMap(mapRef.current);

        setReady(true);
        report("Kerala fleet view · live traffic on");

        if (navigator.geolocation) {
          navigator.geolocation.getCurrentPosition(
            (position) => {
              if (cancelled || !mapRef.current) return;
              const here = {
                lat: position.coords.latitude,
                lng: position.coords.longitude,
              };
              new google.maps.Marker({
                position: here,
                map: mapRef.current,
                icon: {
                  path: google.maps.SymbolPath.CIRCLE,
                  scale: 7,
                  fillColor: "#51d29d",
                  fillOpacity: 1,
                  strokeColor: "#0b1018",
                  strokeWeight: 3,
                },
                title: "Your location",
                zIndex: 40,
              });
              mapRef.current.setCenter(here);
              mapRef.current.setZoom(9);
              report("Centred on your location");
            },
            () => report("Location unavailable — showing the Kerala fleet view"),
            { timeout: 8000 }
          );
        }
      })
      .catch((exc) => {
        if (cancelled) return;
        setFailure(exc.message);
        onError?.(exc.message);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // --- fleet markers, clustered ------------------------------------------
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const google = window.google;

    clustererRef.current?.clearMarkers();
    fleetMarkersRef.current.forEach((marker) => marker.setMap(null));

    fleetMarkersRef.current = visibleTrucks
      .filter((truck) => truck.position)
      .map((truck) => {
        const marker = new google.maps.Marker({
          position: truck.position,
          icon: pinIcon(
            truck.id === selectedTruck?.id
              ? SELECTED_COLOUR
              : STATUS_COLOUR[truck.status] || "#7886ff"
          ),
          title: `${truck.id} · ${truck.route} · ${truck.status}`,
          zIndex: truck.id === selectedTruck?.id ? 30 : 10,
        });
        if (onSelectTruck) {
          marker.addListener("click", () => onSelectTruck(truck));
        }
        return marker;
      });

    // Clustering keeps the same interaction model as the fleet grows.
    if (!clustererRef.current) {
      clustererRef.current = new MarkerClusterer({ map: mapRef.current });
    }
    clustererRef.current.addMarkers(fleetMarkersRef.current);
  }, [ready, visibleTrucks, selectedTruck, onSelectTruck]);

  // --- routes -------------------------------------------------------------
  useEffect(() => {
    if (!ready || !mapRef.current || !directionsRef.current) return;

    const google = window.google;
    clearOverlays();
    setSuggestions([]);

    const drawStops = (points) => {
      points.forEach((point, index) => {
        overlaysRef.current.push(
          new google.maps.Marker({
            position: point,
            map: mapRef.current,
            icon: pinIcon(STOP_COLOUR, true),
            label: {
              text: String(index + 1),
              color: "#0b1018",
              fontSize: "10px",
              fontWeight: "700",
            },
            zIndex: 25,
          })
        );
      });
    };

    // Manual planner mode.
    if (routeRequest?.origin && routeRequest?.destination) {
      report(`Routing ${routeRequest.origin} → ${routeRequest.destination}`);

      directionsRef.current.route(
        {
          origin: routeRequest.origin,
          destination: routeRequest.destination,
          travelMode: google.maps.TravelMode.DRIVING,
          provideRouteAlternatives: true,
        },
        (response, responseStatus) => {
          if (responseStatus !== "OK" || !response?.routes?.length) {
            report(`No driving route found (${responseStatus})`);
            return;
          }

          setSuggestions(
            response.routes.map((route, index) => ({
              index,
              summary: route.summary || `Option ${index + 1}`,
              distance: route.legs?.[0]?.distance?.text || "—",
              duration: route.legs?.[0]?.duration?.text || "—",
            }))
          );

          const active = Math.min(chosen, response.routes.length - 1);

          response.routes.forEach((route, index) => {
            overlaysRef.current.push(
              new google.maps.Polyline({
                path: route.overview_path,
                map: mapRef.current,
                strokeColor:
                  index === active ? SELECTED_COLOUR : ALTERNATE_COLOUR,
                strokeOpacity: index === active ? 0.95 : 0.5,
                strokeWeight: index === active ? 5 : 3,
                zIndex: index === active ? 20 : 5,
              })
            );
          });

          const selected = response.routes[active];

          // Route nodes: the end of each manoeuvre on the chosen route.
          const nodes = [];
          selected.legs?.forEach((leg) =>
            leg.steps?.forEach((step) => nodes.push(step.end_location))
          );
          nodes.slice(0, -1).forEach((node) => {
            overlaysRef.current.push(
              new google.maps.Marker({
                position: node,
                map: mapRef.current,
                icon: {
                  path: google.maps.SymbolPath.CIRCLE,
                  scale: 3.4,
                  fillColor: STOP_COLOUR,
                  fillOpacity: 0.9,
                  strokeWeight: 0,
                },
                zIndex: 15,
              })
            );
          });

          const start = selected.legs?.[0]?.start_location;
          const end = selected.legs?.[selected.legs.length - 1]?.end_location;
          [
            [start, "S", "#7886ff"],
            [end, "D", "#fb8497"],
          ].forEach(([position, text, colour]) => {
            if (!position) return;
            overlaysRef.current.push(
              new google.maps.Marker({
                position,
                map: mapRef.current,
                icon: pinIcon(colour, true),
                label: {
                  text,
                  color: "#0b1018",
                  fontSize: "10px",
                  fontWeight: "700",
                },
                zIndex: 35,
              })
            );
          });

          const mapKey = `request_${routeRequest.origin}_${routeRequest.destination}_${active}`;
          if (lastFittedKeyRef.current !== mapKey) {
            const bounds = new google.maps.LatLngBounds();
            selected.overview_path.forEach((point) => bounds.extend(point));
            mapRef.current.fitBounds(bounds, 56);
            lastFittedKeyRef.current = mapKey;
          }

          report(
            `${response.routes.length} option${response.routes.length === 1 ? "" : "s"} · ` +
              `${selected.legs?.[0]?.distance?.text || "—"}, ${selected.legs?.[0]?.duration?.text || "—"}`
          );
        }
      );
      return;
    }

    // Planned-truck mode: follow the stops the platform actually chose.
    const stops = selectedTruck?.stops || [];
    if (stops.length < 2) {
      if (selectedTruck) report(`${selectedTruck.id} has no mapped stops`);
      return;
    }

    const points = stops
      .map((stop) => {
        if (typeof stop === "string") return stop;
        if (stop.lat != null && stop.lng != null && !isNaN(stop.lat) && !isNaN(stop.lng)) {
          return { lat: Number(stop.lat), lng: Number(stop.lng) };
        }
        if (stop.name) return stop.name;
        return null;
      })
      .filter(Boolean);

    directionsRef.current.route(
      {
        origin: points[0],
        destination: points[points.length - 1],
        waypoints: points.slice(1, -1).map((location) => ({
          location,
          stopover: true,
        })),
        travelMode: google.maps.TravelMode.DRIVING,
      },
      (response, responseStatus) => {
        if (responseStatus !== "OK" || !response?.routes?.length) {
          report(`Could not draw ${selectedTruck.id}'s route (${responseStatus})`);
          // Still show the stops so the lane is visible.
          drawStops(points);
          return;
        }

        overlaysRef.current.push(
          new google.maps.Polyline({
            path: response.routes[0].overview_path,
            map: mapRef.current,
            strokeColor: SELECTED_COLOUR,
            strokeOpacity: 0.95,
            strokeWeight: 5,
            zIndex: 20,
          })
        );

        drawStops(points);

        const mapKey = `truck_${selectedTruck.id}`;
        if (lastFittedKeyRef.current !== mapKey) {
          const bounds = new google.maps.LatLngBounds();
          response.routes[0].overview_path.forEach((point) => bounds.extend(point));
          mapRef.current.fitBounds(bounds, 56);
          lastFittedKeyRef.current = mapKey;
        }

        report(
          `${selectedTruck.id} · ${stops.length} planned stops · ${selectedTruck.route}`
        );
      }
    );
  }, [ready, routeRequest, selectedTruck, chosen, clearOverlays, report]);

  useEffect(() => setChosen(0), [routeRequest]);

  const zoom = (delta) => {
    if (!mapRef.current) return;
    mapRef.current.setZoom((mapRef.current.getZoom() || 7) + delta);
  };

  return (
    <div className="map-shell">
      {failure ? (
        <div className="map-empty">
          <div>
            <strong style={{ display: "block", marginBottom: 6 }}>
              Map unavailable
            </strong>
            {failure}
          </div>
        </div>
      ) : (
        <div ref={containerRef} className="map-canvas" role="application" aria-label="Fleet map" />
      )}

      <div className="map-overlay map-status">
        {showFleetControls && selectedTruck && onBackToFleet && (
          <button
            type="button"
            onClick={onBackToFleet}
            title="Back to fleet view"
            aria-label="Back to fleet view"
            style={{ display: "grid", placeItems: "center", color: "var(--cyan)" }}
          >
            <ArrowLeft size={14} />
          </button>
        )}
        <Navigation size={13} aria-hidden="true" style={{ color: "var(--cyan)" }} />
        <span>{failure ? "Offline" : status}</span>
      </div>

      {showFleetControls && (
        <>
          <div className="map-overlay map-search">
            <Search size={13} aria-hidden="true" style={{ color: "var(--text-faint)" }} />
            <label className="sr-only" htmlFor="map-truck-search">
              Find a vehicle on the map
            </label>
            <input
              id="map-truck-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Find truck, driver, cargo…"
            />
          </div>

          <div className="map-overlay map-filters" role="group" aria-label="Map filters">
            {FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                className={`map-chip ${filter === item.key ? "active" : ""}`}
                onClick={() => setFilter(item.key)}
                aria-pressed={filter === item.key}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="map-overlay fleet-summary">
            <span className="legend-key">
              <i className="legend-swatch" style={{ background: STATUS_COLOUR["On route"] }} />
              {summary.active} active
            </span>
            <span className="legend-key">
              <i className="legend-swatch" style={{ background: STATUS_COLOUR.Delayed }} />
              {summary.delayed} delayed
            </span>
            <span className="legend-key">
              <i className="legend-swatch" style={{ background: STATUS_COLOUR["At depot"] }} />
              {summary.depot} depot
            </span>
            <span className="legend-key" style={{ color: "var(--rose)" }}>
              {summary.critical} blocked
            </span>
          </div>
        </>
      )}

      <div className="map-overlay map-controls">
        <button type="button" onClick={() => zoom(1)} title="Zoom in" aria-label="Zoom in">
          <Plus size={14} />
        </button>
        <button type="button" onClick={() => zoom(-1)} title="Zoom out" aria-label="Zoom out">
          <Minus size={14} />
        </button>
        <button
          type="button"
          onClick={() => mapRef.current?.setCenter(KERALA_CENTRE)}
          title="Recentre on Kerala"
          aria-label="Recentre on Kerala"
        >
          <Crosshair size={14} />
        </button>
        <button type="button" title="Layers" aria-label="Layers">
          <Layers size={14} />
        </button>
      </div>

      <div className="map-overlay map-legend">
        <span className="legend-key">
          <i className="legend-swatch" style={{ background: SELECTED_COLOUR }} />
          selected route
        </span>
        <span className="legend-key">
          <i className="legend-swatch" style={{ background: ALTERNATE_COLOUR }} />
          alternative
        </span>
        <span className="legend-key">
          <i className="legend-swatch" style={{ background: STOP_COLOUR }} />
          stop
        </span>
        <span className="legend-key">
          <i className="legend-swatch" style={{ background: "#7886ff" }} />
          vehicle
        </span>
      </div>

      {suggestions.length > 0 && (
        <div className="map-overlay suggestions">
          <h4>Route suggestions</h4>
          {suggestions.map((option) => (
            <button
              key={option.index}
              type="button"
              className={`suggestion ${option.index === chosen ? "active" : ""}`}
              onClick={() => setChosen(option.index)}
            >
              <strong>
                Option {option.index + 1} · {option.summary}
              </strong>
              {option.distance} · {option.duration}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
