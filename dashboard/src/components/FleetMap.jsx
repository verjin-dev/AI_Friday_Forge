import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MarkerClusterer } from "@googlemaps/markerclusterer";
import L from "leaflet";
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

const MAP_PROVIDERS = [
  { key: "google", label: "Google" },
  { key: "osm", label: "OSM" },
];

const KERALA_VIEWBOX = "74.7,12.9,77.9,8.0";

function formatDistance(meters) {
  if (!Number.isFinite(meters)) return "-";
  if (meters < 1000) return `${Math.round(meters)} m`;
  return `${(meters / 1000).toFixed(meters >= 10000 ? 0 : 1)} km`;
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "-";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  if (!hours) return `${Math.max(minutes, 1)} min`;
  return `${hours} hr ${minutes ? `${minutes} min` : ""}`.trim();
}

async function geocodeOsmLocation(name, signal) {
  const params = new URLSearchParams({
    q: `${name}, Kerala, India`,
    format: "jsonv2",
    limit: "1",
    countrycodes: "in",
    viewbox: KERALA_VIEWBOX,
    bounded: "1",
  });
  const response = await fetch(`https://nominatim.openstreetmap.org/search?${params}`, {
    signal,
  });
  if (!response.ok) throw new Error(`Could not geocode ${name}`);
  const [match] = await response.json();
  if (!match) throw new Error(`No OpenStreetMap match for ${name}`);
  return { lat: Number(match.lat), lng: Number(match.lon), name };
}

async function fetchOsmRoutes(points, signal) {
  const coords = points.map((point) => `${point.lng},${point.lat}`).join(";");
  const params = new URLSearchParams({
    overview: "full",
    geometries: "geojson",
    steps: "true",
    alternatives: "true",
  });
  const response = await fetch(
    `https://router.project-osrm.org/route/v1/driving/${coords}?${params}`,
    { signal }
  );
  if (!response.ok) throw new Error("OpenStreetMap route service is unavailable");
  const payload = await response.json();
  if (payload.code !== "Ok" || !payload.routes?.length) {
    throw new Error(payload.message || "No OpenStreetMap driving route found");
  }
  return payload.routes;
}

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

function osmIcon(colour, label) {
  return L.divIcon({
    className: "osm-pin",
    html: `<span style="--pin:${colour}">${label || ""}</span>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

function OpenStreetMap({
  visibleTrucks,
  selectedTruck,
  routeRequest,
  chosen,
  onSuggestions,
  onSelectTruck,
  onStatus,
  actionsRef,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const layerRef = useRef(null);
  const locationRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return undefined;

    mapRef.current = L.map(containerRef.current, {
      center: [KERALA_CENTRE.lat, KERALA_CENTRE.lng],
      zoom: 7,
      zoomControl: false,
      attributionControl: false,
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(mapRef.current);

    L.control.attribution({ prefix: false }).addTo(mapRef.current);
    layerRef.current = L.layerGroup().addTo(mapRef.current);
    actionsRef.current = {
      zoom: (delta) => mapRef.current?.setZoom((mapRef.current.getZoom() || 7) + delta),
      recenter: () => mapRef.current?.setView([KERALA_CENTRE.lat, KERALA_CENTRE.lng], 7),
    };

    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          if (!mapRef.current) return;
          const here = [position.coords.latitude, position.coords.longitude];
          locationRef.current = L.marker(here, {
            icon: osmIcon("#51d29d"),
            title: "Your location",
          }).addTo(mapRef.current);
          mapRef.current.setView(here, 9);
          onStatus?.("Centred on your location");
        },
        () => onStatus?.("Location unavailable - showing the Kerala fleet view"),
        { timeout: 8000 }
      );
    }

    setTimeout(() => mapRef.current?.invalidateSize(), 80);

    return () => {
      actionsRef.current = null;
      locationRef.current = null;
      mapRef.current?.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, [actionsRef, onStatus]);

  useEffect(() => {
    if (!mapRef.current || !layerRef.current) return undefined;

    const controller = new AbortController();
    let cancelled = false;
    layerRef.current.clearLayers();
    const bounds = [];

    const addStop = (point, index) => {
      const latLng = [point.lat, point.lng];
      bounds.push(latLng);
      L.marker(latLng, {
        icon: osmIcon(STOP_COLOUR, String(index + 1)),
        title: point.name || `Stop ${index + 1}`,
      }).addTo(layerRef.current);
    };

    const drawRoute = (route, colour, active) => {
      const latLngs = route.geometry.coordinates.map(([lng, lat]) => [lat, lng]);
      latLngs.forEach((latLng) => bounds.push(latLng));
      L.polyline(latLngs, {
        color: colour,
        weight: active ? 5 : 3,
        opacity: active ? 0.92 : 0.5,
        lineCap: "round",
      }).addTo(layerRef.current);
    };

    const fitMap = () => {
      if (bounds.length > 1) {
        mapRef.current.fitBounds(bounds, { padding: [56, 56], maxZoom: 11 });
      } else if (bounds.length === 1) {
        mapRef.current.setView(bounds[0], 9);
      } else {
        mapRef.current.setView([KERALA_CENTRE.lat, KERALA_CENTRE.lng], 7);
      }
    };

    const drawFleet = () => {
      onSuggestions?.([]);
      visibleTrucks
        .filter((truck) => truck.position)
        .forEach((truck) => {
          const colour =
            truck.id === selectedTruck?.id
              ? SELECTED_COLOUR
              : STATUS_COLOUR[truck.status] || "#7886ff";
          const latLng = [truck.position.lat, truck.position.lng];
          bounds.push(latLng);
          const marker = L.marker(latLng, {
            icon: osmIcon(colour),
            title: `${truck.id} - ${truck.route} - ${truck.status}`,
          }).addTo(layerRef.current);
          if (onSelectTruck) marker.on("click", () => onSelectTruck(truck));
        });
      fitMap();
      onStatus?.("OpenStreetMap fleet view");
    };

    const drawSelectedStops = async (stops) => {
      onSuggestions?.([]);
      const points = stops
        .filter((stop) => Number.isFinite(stop.lat) && Number.isFinite(stop.lng))
        .map((stop) => ({ lat: stop.lat, lng: stop.lng, name: stop.name }));
      try {
        const routes = await fetchOsmRoutes(points, controller.signal);
        if (cancelled) return;
        drawRoute(routes[0], SELECTED_COLOUR, true);
      } catch {
        if (cancelled) return;
        const latLngs = points.map((point) => [point.lat, point.lng]);
        latLngs.forEach((latLng) => bounds.push(latLng));
        L.polyline(latLngs, {
          color: SELECTED_COLOUR,
          weight: 5,
          opacity: 0.92,
          lineCap: "round",
        }).addTo(layerRef.current);
      }
      points.forEach(addStop);
      fitMap();
      onStatus?.(`${selectedTruck.id} - ${stops.length} planned stops - OpenStreetMap`);
    };

    const drawRouteRequest = async () => {
      onStatus?.(`Routing ${routeRequest.origin} -> ${routeRequest.destination}`);
      const points = await Promise.all([
        geocodeOsmLocation(routeRequest.origin, controller.signal),
        geocodeOsmLocation(routeRequest.destination, controller.signal),
      ]);
      if (cancelled) return;

      const routes = await fetchOsmRoutes(points, controller.signal);
      if (cancelled) return;
      const active = Math.min(chosen, routes.length - 1);

      onSuggestions?.(
        routes.map((route, index) => ({
          index,
          summary: index === 0 ? "Best OSM route" : `OSM alternative ${index + 1}`,
          distance: formatDistance(route.distance),
          duration: formatDuration(route.duration),
        }))
      );
      routes.forEach((route, index) =>
        drawRoute(route, index === active ? SELECTED_COLOUR : ALTERNATE_COLOUR, index === active)
      );
      points.forEach(addStop);
      fitMap();

      const route = routes[active];
      onStatus?.(
        `${routes.length} option${routes.length === 1 ? "" : "s"} - ` +
          `${formatDistance(route.distance)}, ${formatDuration(route.duration)} - OpenStreetMap`
      );
    };

    if (routeRequest?.origin && routeRequest?.destination) {
      drawRouteRequest().catch((exc) => {
        if (cancelled) return;
        onSuggestions?.([]);
        onStatus?.(exc.message);
      });
    } else {
      const stops = selectedTruck?.stops || [];
      if (stops.length >= 2) drawSelectedStops(stops);
      else drawFleet();
    }

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [
    visibleTrucks,
    selectedTruck,
    routeRequest,
    chosen,
    onSuggestions,
    onSelectTruck,
    onStatus,
  ]);

  return (
    <div
      ref={containerRef}
      className="map-canvas osm-canvas"
      role="application"
      aria-label="OpenStreetMap fleet map"
    />
  );
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
  mapProvider,
  onMapProviderChange,
}) {
  const [localProvider, setLocalProvider] = useState("google");
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const overlaysRef = useRef([]);
  const fleetMarkersRef = useRef([]);
  const clustererRef = useRef(null);
  const directionsRef = useRef(null);
  const trafficRef = useRef(null);
  const osmActionsRef = useRef(null);

  const routeActiveRef = useRef(false);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState(null);
  const [status, setStatus] = useState("Loading map…");
  const [suggestions, setSuggestions] = useState([]);
  const [chosen, setChosen] = useState(0);
  const [query, setQuery] = useState("");

  const provider = mapProvider || localProvider;
  const setProvider = useCallback(
    (next) => {
      if (onMapProviderChange) onMapProviderChange(next);
      else setLocalProvider(next);
    },
    [onMapProviderChange]
  );

  const visibleTrucks = useMemo(() => {
    const term = query.trim().toLowerCase();
    return trucks.filter((truck) => {
      if (!term) return true;
      return [truck.id, truck.driver, truck.route, truck.load, truck.vehicle]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(term));
    });
  }, [trucks, query]);

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
  useEffect(() => {
    if (provider !== "google") return undefined;
    let cancelled = false;

    loadGoogleMaps()
      .then((google) => {
        if (cancelled || !containerRef.current) return;

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
              if (!routeActiveRef.current) {
                mapRef.current.setCenter(here);
                mapRef.current.setZoom(9);
                report("Centred on your location");
              }
            },
            () => {
              if (!routeActiveRef.current) {
                report("Location unavailable — showing the Kerala fleet view");
              }
            },
            { timeout: 8000 }
          );
        }
      })
      .catch((exc) => {
        if (cancelled) return;
        setFailure(exc.message);
        onErrorRef.current?.(exc.message);
      });

    return () => {
      cancelled = true;
    };
  }, [provider, report]);

  // --- fleet markers, clustered ------------------------------------------
  useEffect(() => {
    if (provider !== "google" || !ready || !mapRef.current) return;
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
  }, [provider, ready, visibleTrucks, selectedTruck, onSelectTruck]);

  // --- routes -------------------------------------------------------------
  useEffect(() => {
    if (provider !== "google" || !ready || !mapRef.current || !directionsRef.current) return;

    const google = window.google;
    clearOverlays();
    setSuggestions([]);
    routeActiveRef.current = false;

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
      routeActiveRef.current = true;
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

          const bounds = new google.maps.LatLngBounds();
          selected.overview_path.forEach((point) => bounds.extend(point));
          mapRef.current.fitBounds(bounds, 56);
          routeActiveRef.current = true;

          report(
            `${response.routes.length} option${response.routes.length === 1 ? "" : "s"} · ` +
              `${selected.legs?.[0]?.distance?.text || "—"}, ${selected.legs?.[0]?.duration?.text || "—"}`
          );
        }
      );
      return;
    }

    // Planned-truck mode: use real-time Google Maps directions for the
    // stops the platform chose. Accepts both lat/lng coords and stop names
    // so the route always renders with live traffic data.
    const stops = selectedTruck?.stops || [];
    if (stops.length < 2) {
      if (selectedTruck) report(`${selectedTruck.id} has no mapped stops`);
      return;
    }

    const toLocation = (stop) => {
      if (Number.isFinite(stop.lat) && Number.isFinite(stop.lng))
        return { lat: stop.lat, lng: stop.lng };
      return `${stop.name}, Kerala, India`;
    };

    const locations = stops
      .filter(
        (stop) =>
          (Number.isFinite(stop.lat) && Number.isFinite(stop.lng)) || stop.name
      )
      .map(toLocation);

    if (locations.length < 2) {
      report(`${selectedTruck.id} has no mappable stops`);
      return;
    }

    routeActiveRef.current = true;
    report(`Routing ${selectedTruck.id} · ${stops.length} stops via live traffic…`);

    directionsRef.current.route(
      {
        origin: locations[0],
        destination: locations[locations.length - 1],
        waypoints: locations.slice(1, -1).map((location) => ({
          location,
          stopover: true,
        })),
        travelMode: google.maps.TravelMode.DRIVING,
        drivingOptions: {
          departureTime: new Date(),
          trafficModel: google.maps.TrafficModel.BEST_GUESS,
        },
      },
      (response, responseStatus) => {
        if (responseStatus !== "OK" || !response?.routes?.length) {
          report(`Could not draw ${selectedTruck.id}'s route (${responseStatus})`);
          return;
        }

        const route = response.routes[0];

        overlaysRef.current.push(
          new google.maps.Polyline({
            path: route.overview_path,
            map: mapRef.current,
            strokeColor: SELECTED_COLOUR,
            strokeOpacity: 0.95,
            strokeWeight: 5,
            zIndex: 20,
          })
        );

        // Place numbered stop markers at the real positions resolved by
        // DirectionsService (leg start/end), not the input coordinates.
        const legPositions = [];
        route.legs.forEach((leg, i) => {
          legPositions.push(leg.start_location);
          if (i === route.legs.length - 1) legPositions.push(leg.end_location);
        });
        legPositions.forEach((pos, idx) => {
          overlaysRef.current.push(
            new google.maps.Marker({
              position: pos,
              map: mapRef.current,
              icon: pinIcon(STOP_COLOUR, true),
              label: {
                text: String(idx + 1),
                color: "#0b1018",
                fontSize: "10px",
                fontWeight: "700",
              },
              title: stops[idx]?.name || `Stop ${idx + 1}`,
              zIndex: 25,
            })
          );
        });

        const bounds = new google.maps.LatLngBounds();
        route.overview_path.forEach((point) => bounds.extend(point));
        mapRef.current.fitBounds(bounds, 56);
        routeActiveRef.current = true;

        // Sum up real distance/duration from all legs
        let totalDist = 0;
        let totalDur = 0;
        route.legs.forEach((leg) => {
          totalDist += leg.distance?.value || 0;
          totalDur += leg.duration_in_traffic?.value || leg.duration?.value || 0;
        });

        report(
          `${selectedTruck.id} · ${stops.length} planned stops · ${selectedTruck.route} · ` +
            `${formatDistance(totalDist)}, ${formatDuration(totalDur)} (live)`
        );
      }
    );
  }, [provider, ready, routeRequest, selectedTruck, chosen, clearOverlays, report]);

  useEffect(() => setChosen(0), [routeRequest]);

  const zoom = (delta) => {
    if (!mapRef.current) return;
    mapRef.current.setZoom((mapRef.current.getZoom() || 7) + delta);
  };

  return (
    <div className="map-shell">
      {provider === "osm" ? (
        <OpenStreetMap
          visibleTrucks={visibleTrucks}
          selectedTruck={selectedTruck}
          routeRequest={routeRequest}
          chosen={chosen}
          onSuggestions={setSuggestions}
          onSelectTruck={onSelectTruck}
          onStatus={report}
          actionsRef={osmActionsRef}
        />
      ) : failure ? (
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

      <div className="map-topbar" aria-label="Map tools">
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
          <span>{provider === "google" && failure ? "Offline" : status}</span>
        </div>

        {showFleetControls && (
          <div className="map-overlay map-search">
            <Search size={13} aria-hidden="true" style={{ color: "var(--text-faint)" }} />
            <label className="sr-only" htmlFor="map-truck-search">
              Find a vehicle on the map
            </label>
            <input
              id="map-truck-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Find truck, driver, cargo..."
            />
          </div>
        )}

        <div className="map-overlay map-provider" role="group" aria-label="Map provider">
          {MAP_PROVIDERS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={provider === item.key ? "active" : ""}
              onClick={() => setProvider(item.key)}
              aria-pressed={provider === item.key}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {showFleetControls && (
        <>
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
        <button
          type="button"
          onClick={() =>
            provider === "google" ? zoom(1) : osmActionsRef.current?.zoom(1)
          }
          title="Zoom in"
          aria-label="Zoom in"
        >
          <Plus size={14} />
        </button>
        <button
          type="button"
          onClick={() =>
            provider === "google" ? zoom(-1) : osmActionsRef.current?.zoom(-1)
          }
          title="Zoom out"
          aria-label="Zoom out"
        >
          <Minus size={14} />
        </button>
        <button
          type="button"
          onClick={() =>
            provider === "google"
              ? mapRef.current?.setCenter(KERALA_CENTRE)
              : osmActionsRef.current?.recenter()
          }
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
