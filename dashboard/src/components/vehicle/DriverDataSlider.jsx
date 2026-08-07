import {
  AlertTriangle,
  Gauge,
  LayoutDashboard,
  MessageSquare,
  Moon,
  Phone,
  Route as RouteIcon,
  Sun,
  TriangleAlert,
} from "lucide-react";
import { Autoplay, Keyboard } from "swiper/modules";
import { Swiper, SwiperSlide } from "swiper/react";
import "swiper/css";

import { ROLES } from "../../config/demoAuth.js";
import TripTimeline from "./TripTimeline.jsx";

function KV({ k, v, muted }) {
  return (
    <div className="kv-item">
      <span className="k">{k}</span>
      <span className={`v ${muted ? "muted" : ""}`}>{v}</span>
    </div>
  );
}

export default function DriverDataSlider({
  truck,
  alerts,
  session,
  theme,
  onNavigate,
  onNotify,
  onToggleTheme,
}) {
  return (
    <section className="card cockpit-slider-card">
      <Swiper
        className="cockpit-slides"
        aria-label="Driver data slider"
        modules={[Autoplay, Keyboard]}
        autoplay={{
          delay: 3000,
          disableOnInteraction: false,
          pauseOnMouseEnter: true,
        }}
        keyboard={{ enabled: true }}
        loop
        slidesPerView={1}
        spaceBetween={10}
      >
        <SwiperSlide>
        <article className="cockpit-slide">
          <div className="slide-head">
            <RouteIcon size={15} aria-hidden="true" style={{ color: "var(--cyan)" }} />
            <h4>Trip timeline</h4>
            <span>{truck?.timeline?.length || 0} stops</span>
          </div>
          <TripTimeline timeline={truck?.timeline} />
        </article>
        </SwiperSlide>

        <SwiperSlide>
        <article className="cockpit-slide">
          <div className="slide-head">
            <h4>Delivery</h4>
          </div>
          <div className="kv-list compact">
            <KV k="Route" v={truck?.route || "-"} />
            <KV k="Next stop" v={truck?.next_stop || "-"} />
            <KV k="Distance left" v={`${truck?.distance_remaining_km ?? 0} km`} />
            <KV k="Departure" v={truck?.departure || "-"} />
            <KV k="Planned arrival" v={truck?.eta || "-"} />
          </div>
        </article>
        </SwiperSlide>

        <SwiperSlide>
        <article className="cockpit-slide">
          <div className="slide-head">
            <h4>Cargo</h4>
          </div>
          <div className="kv-list compact">
            <KV k="Shipment" v={truck?.load || "-"} />
            <KV
              k="Vehicle capacity"
              v={
                truck?.cargo?.capacity_kg
                  ? `${truck.cargo.capacity_kg} kg / ${truck.cargo.capacity_m3} m3`
                  : "-"
              }
            />
            <KV k="Refrigerated" v={truck?.cargo?.refrigerated ? "yes" : "no"} />
            <KV k="Hazmat certified" v={truck?.cargo?.hazmat_certified ? "yes" : "no"} />
            <KV k="Cargo temperature" v="not tracked" muted />
          </div>
        </article>
        </SwiperSlide>

        <SwiperSlide>
        <article className="cockpit-slide">
          <div className="slide-head">
            <Gauge size={15} aria-hidden="true" style={{ color: "var(--indigo)" }} />
            <h4>Arrival forecast</h4>
          </div>
          <div className="kv-list compact">
            <KV
              k="On-time probability"
              v={
                truck?.predictive
                  ? `${(truck.predictive.on_time_probability * 100).toFixed(0)}%`
                  : "-"
              }
            />
            <KV
              k="Delay probability"
              v={
                truck?.predictive
                  ? `${(truck.predictive.delay_probability * 100).toFixed(0)}%`
                  : "-"
              }
            />
            <KV
              k="Model confidence"
              v={
                truck?.predictive
                  ? `${(truck.predictive.confidence * 100).toFixed(0)}%`
                  : "-"
              }
            />
            <KV k="Baseline" v={truck?.predictive?.basis || "-"} />
          </div>
          {truck?.predictive?.note && <p className="provenance">{truck.predictive.note}</p>}
        </article>
        </SwiperSlide>

        <SwiperSlide>
        <article className="cockpit-slide">
          <div className="slide-head">
            <TriangleAlert size={15} aria-hidden="true" style={{ color: "var(--amber)" }} />
            <h4>Road conditions</h4>
            <span>{alerts.length}</span>
          </div>
          {alerts.length > 0 ? (
            alerts.map((alert) => (
              <div
                key={alert.id}
                className={`route-alert ${alert.severity === "critical" ? "critical" : ""}`}
              >
                <AlertTriangle
                  size={15}
                  aria-hidden="true"
                  style={{
                    flex: "none",
                    marginTop: 2,
                    color: alert.severity === "critical" ? "var(--rose)" : "var(--amber)",
                  }}
                />
                <span>
                  <strong style={{ display: "block", fontSize: 12.5 }}>{alert.title}</strong>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    {alert.detail}
                  </span>
                </span>
              </div>
            ))
          ) : (
            <p className="empty-state compact-empty">No active route alerts.</p>
          )}
        </article>
        </SwiperSlide>

        <SwiperSlide>
        <article className="cockpit-slide">
          <div className="slide-head">
            <h4>Vehicle health</h4>
          </div>
          <div className="kv-list compact">
            <KV k="Assigned profile" v={truck?.vehicle || "-"} />
            {(truck?.untracked || []).slice(0, 4).map((field) => (
              <KV key={field} k={field.replace(/_/g, " ")} v="not tracked" muted />
            ))}
          </div>
          <p className="provenance">Connect telematics to populate live health metrics.</p>
        </article>
        </SwiperSlide>

        <SwiperSlide>
        <article className="cockpit-slide">
          <div className="slide-head">
            <h4>Quick actions</h4>
          </div>
          <div className="driver-actions compact-actions">
            <button
              type="button"
              className="pill-btn"
              onClick={() => onNotify("Calling dispatch...", "info")}
            >
              <Phone size={14} aria-hidden="true" />
              Call dispatch
            </button>
            <button
              type="button"
              className="pill-btn"
              onClick={() => onNotify("Message sent to dispatch.", "success")}
            >
              <MessageSquare size={14} aria-hidden="true" />
              Message
            </button>
            <button
              type="button"
              className="pill-btn"
              onClick={() =>
                onNotify(
                  "Re-route requested - dispatch will confirm from the operations desk.",
                  "info"
                )
              }
            >
              <RouteIcon size={14} aria-hidden="true" />
              Request re-route
            </button>
            {session?.role === ROLES.ADMIN && (
              <button type="button" className="pill-btn" onClick={() => onNavigate("/")}>
                <LayoutDashboard size={14} aria-hidden="true" />
                Operations
              </button>
            )}
            <button type="button" className="pill-btn" onClick={onToggleTheme}>
              {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
              Theme
            </button>
          </div>
        </article>
        </SwiperSlide>
      </Swiper>
    </section>
  );
}
