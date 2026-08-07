import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import {
  canAccess,
  clearSession,
  landingFor,
  loadSession,
  saveSession,
} from "./config/demoAuth.js";

const THEME_KEY = "logipilot.theme";

function getInitialTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    // Storage can be unavailable in private browsing.
  }

  return window.matchMedia?.("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

// Lazy so the driver console and login visuals are not in the admin's
// first paint, and vice versa.
const LoginPage = lazy(() => import("./pages/LoginPage.jsx"));
const App = lazy(() => import("./App.jsx"));
const VehicleDashboard = lazy(() => import("./pages/VehicleDashboard.jsx"));

const MESSAGES = {
  "/login": "Preparing secure access…",
  "/": "Loading fleet operations…",
  "/vehicle": "Starting the vehicle console…",
};

function RouteLoader({ path }) {
  return (
    <div className="route-loader" role="status" aria-live="polite">
      <Loader2 size={20} className="spin" aria-hidden="true" />
      <span>{MESSAGES[path] || "Loading…"}</span>
    </div>
  );
}

/** Minimal history-based router — three routes do not warrant a dependency. */
export default function Root() {
  const [path, setPath] = useState(window.location.pathname);
  const [session, setSession] = useState(() => loadSession());
  const [theme, setTheme] = useState(getInitialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // Non-critical preference persistence.
    }
  }, [theme]);

  const onToggleTheme = useCallback(() => {
    setTheme((value) => (value === "dark" ? "light" : "dark"));
  }, []);

  const navigate = useCallback((next, { replace = false } = {}) => {
    if (replace) window.history.replaceState({}, "", next);
    else window.history.pushState({}, "", next);
    setPath(next);
  }, []);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Guard: send unauthenticated visitors to login, and bounce anyone who
  // reaches a workspace their role cannot use.
  useEffect(() => {
    if (path === "/login") {
      if (session) navigate(landingFor(session), { replace: true });
      return;
    }
    if (!session) {
      navigate("/login", { replace: true });
      return;
    }
    if (!canAccess(session, path)) {
      navigate(landingFor(session), { replace: true });
    }
  }, [path, session, navigate]);

  const onSignIn = useCallback(
    (nextSession) => {
      saveSession(nextSession);
      setSession(nextSession);
      navigate(nextSession.landing, { replace: true });
    },
    [navigate]
  );

  const onSignOut = useCallback(() => {
    clearSession();
    setSession(null);
    navigate("/login", { replace: true });
  }, [navigate]);

  const render = () => {
    if (!session || path === "/login") {
      return (
        <LoginPage
          onSignIn={onSignIn}
          theme={theme}
          onToggleTheme={onToggleTheme}
        />
      );
    }
    if (path === "/vehicle") {
      return (
        <VehicleDashboard
          session={session}
          onSignOut={onSignOut}
          onNavigate={navigate}
          theme={theme}
          onToggleTheme={onToggleTheme}
        />
      );
    }
    return (
      <App
        session={session}
        onSignOut={onSignOut}
        onNavigate={navigate}
        theme={theme}
        onToggleTheme={onToggleTheme}
      />
    );
  };

  return (
    <Suspense fallback={<RouteLoader path={path} />}>{render()}</Suspense>
  );
}
