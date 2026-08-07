import {
  BarChart3,
  Bell,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  LayoutGrid,
  LogOut,
  Navigation,
  Route,
  Search,
  Settings,
  Sparkles,
  Truck,
} from "lucide-react";

const PRIMARY = [
  { label: "Overview", icon: LayoutGrid },
  { label: "Fleet", icon: Truck },
  { label: "Routes", icon: Route },
  { label: "Analytics", icon: BarChart3 },
];

const SECONDARY = [
  { label: "Search", icon: Search },
  { label: "Notifications", icon: Bell, dot: true },
  { label: "Settings", icon: Settings },
];

export default function Sidebar({
  activeNav,
  onNavigate,
  collapsed,
  onToggleCollapse,
  alertCount,
  session,
  onSignOut,
  onOpenVehicle,
}) {
  const showLabels = !collapsed;

  const renderItem = ({ label, icon: Icon, dot }) => (
    <button
      key={label}
      type="button"
      className={`nav-item ${activeNav === label ? "active" : ""}`}
      onClick={() => onNavigate(label)}
      title={label}
      aria-label={label}
      aria-current={activeNav === label ? "page" : undefined}
    >
      <Icon size={17} strokeWidth={1.75} aria-hidden="true" />
      {showLabels && <span>{label}</span>}
      {dot && alertCount > 0 && (
        <span className="nav-dot" aria-label={`${alertCount} unread`} />
      )}
    </button>
  );

  // Width is a CSS transition driven by .shell[data-collapsed] — Framer
  // Motion's `animate` prop does not reliably track prop changes on React 19.
  return (
    <nav className="sidebar" aria-label="Primary">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          <Truck size={17} strokeWidth={2} />
        </span>
        {showLabels && (
          <span className="brand-text desktop-only">
            <h1>Logipilot AI</h1>
            <span>Fleet operations</span>
          </span>
        )}
      </div>

      {showLabels && (
        <button type="button" className="workspace-switch desktop-only">
          <span
            style={{
              width: 18,
              height: 18,
              borderRadius: 5,
              background: "linear-gradient(140deg,var(--emerald),var(--cyan))",
              flex: "none",
            }}
            aria-hidden="true"
          />
          <span style={{ flex: 1, textAlign: "left" }}>Kerala South</span>
          <ChevronsUpDown size={13} aria-hidden="true" />
        </button>
      )}

      {showLabels && <p className="nav-caption desktop-only">Operations</p>}
      {PRIMARY.map(renderItem)}

      {showLabels && <p className="nav-caption desktop-only">Workspace</p>}
      <span className="desktop-only" style={{ display: "contents" }}>
        {SECONDARY.map(renderItem)}
      </span>

      {showLabels && (
        <div className="ai-card desktop-only">
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              fontWeight: 600,
            }}
          >
            <Sparkles size={13} aria-hidden="true" /> Logi AI
          </span>
          <p>
            {alertCount > 0
              ? `${alertCount} lane${alertCount === 1 ? "" : "s"} need attention. Open Routes to review the constraint verdicts.`
              : "All lanes are compliant with current incident state."}
          </p>
        </div>
      )}

      {showLabels && onOpenVehicle && (
        <button
          type="button"
          className="nav-item desktop-only"
          onClick={onOpenVehicle}
          title="Open the in-vehicle console"
        >
          <Navigation size={17} strokeWidth={1.75} aria-hidden="true" />
          <span>Vehicle console</span>
        </button>
      )}

      <div className="profile desktop-only">
        <span className="avatar" aria-hidden="true">
          {(session?.name || "OP")
            .split(" ")
            .map((part) => part[0])
            .slice(0, 2)
            .join("")
            .toUpperCase()}
        </span>
        {showLabels && (
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: "block", fontSize: 12.5, fontWeight: 500 }}>
              {session?.name || "Ops Manager"}
            </span>
            <span style={{ fontSize: 11, color: "var(--text-faint)" }}>
              {session?.detail || "Dispatch desk"}
            </span>
          </span>
        )}
        {showLabels && onSignOut && (
          <button
            type="button"
            className="collapse-btn"
            onClick={onSignOut}
            title="Sign out"
            aria-label="Sign out"
          >
            <LogOut size={14} />
          </button>
        )}
        <button
          type="button"
          className="collapse-btn"
          onClick={onToggleCollapse}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      </div>
    </nav>
  );
}
