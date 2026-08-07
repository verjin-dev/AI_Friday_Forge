import {
  Bell,
  ChevronRight,
  LogOut,
  Moon,
  Route,
  Search,
  Sparkles,
  Sun,
} from "lucide-react";

export default function Header({
  title,
  search,
  onSearch,
  theme,
  onToggleTheme,
  onAskAI,
  onQuickRoute,
  onNotifications,
  alertCount,
  session,
  onSignOut,
}) {
  return (
    <header className="topbar card">
      <div>
        <nav className="crumbs" aria-label="Breadcrumb">
          <span>LogiPilot Ai</span>
          <ChevronRight size={11} aria-hidden="true" />
          <span>Operations</span>
          <ChevronRight size={11} aria-hidden="true" />
          <span style={{ color: "var(--text-secondary)" }}>{title}</span>
        </nav>
        <h2>{title}</h2>
      </div>

      <div className="searchbox">
        <Search size={15} aria-hidden="true" style={{ color: "var(--text-faint)" }} />
        <label className="sr-only" htmlFor="fleet-search">
          Search fleet by truck, driver, route or load
        </label>
        <input
          id="fleet-search"
          type="search"
          value={search}
          onChange={(event) => onSearch(event.target.value)}
          placeholder="Search trucks, drivers, routes…"
        />
        <kbd className="kbd">Cmd + K</kbd>
      </div>

      <button type="button" className="pill-btn" onClick={onAskAI}>
        <Sparkles size={14} aria-hidden="true" />
        Ask AI
      </button>

      <button
        type="button"
        className="icon-btn"
        onClick={onQuickRoute}
        title="Plan a route"
        aria-label="Plan a route"
      >
        <Route size={16} />
      </button>

      <button
        type="button"
        className="icon-btn"
        onClick={onToggleTheme}
        title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      >
        {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
      </button>

      <button
        type="button"
        className="icon-btn"
        onClick={onNotifications}
        title="Notifications"
        aria-label={`Notifications, ${alertCount} open`}
        style={{ position: "relative" }}
      >
        <Bell size={16} />
        {alertCount > 0 && (
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              top: 6,
              right: 7,
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "var(--rose)",
            }}
          />
        )}
      </button>

      {session && (
        <span className="pill" title={session.detail}>
          <i className="dot ok" />
          {session.name}
        </span>
      )}

      {onSignOut && (
        <button
          type="button"
          className="icon-btn"
          onClick={onSignOut}
          title="Sign out"
          aria-label="Sign out"
        >
          <LogOut size={16} />
        </button>
      )}
    </header>
  );
}
