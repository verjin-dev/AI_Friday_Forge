/**
 * Demonstration authentication.
 *
 * This is a front-end-only role gate for the demo: it decides which workspace
 * you land in, nothing more. It is NOT security — the credentials live in the
 * bundle, anyone can read them, and the API behind it is unauthenticated.
 *
 * Before this goes anywhere real, replace `authenticate` with a call to a
 * server that issues a session, and enforce the role server-side. The API
 * already models roles (see /api/roles); they are simply not enforced yet.
 */

const SESSION_KEY = "logipilot.session";

export const ROLES = {
  ADMIN: "admin",
  VEHICLE: "vehicle",
};

/** Demo accounts, shown on the login screen so nothing is hidden. */
export const DEMO_ACCOUNTS = [
  {
    username: "ops",
    password: "logipilot",
    role: ROLES.ADMIN,
    name: "Ops Manager",
    detail: "Dispatch desk · Kerala South",
    landing: "/",
    // Maps onto the backend RBAC role used for tool permissions.
    apiRole: "ops_manager",
  },
  {
    username: "driver",
    password: "logipilot",
    role: ROLES.VEHICLE,
    name: "Vehicle Console",
    detail: "In-vehicle console",
    landing: "/vehicle",
    apiRole: "dispatcher",
  },
];

export function authenticate(username, password) {
  const account = DEMO_ACCOUNTS.find(
    (item) =>
      item.username.toLowerCase() === String(username).trim().toLowerCase() &&
      item.password === password
  );
  if (!account) return null;

  const { password: _password, ...session } = account;
  return session;
}

export function saveSession(session) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    // Private browsing can refuse storage; the app still works for this tab.
  }
}

export function loadSession() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function clearSession() {
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // Nothing to clear.
  }
}

/** Which route a role is allowed to land on. */
export function landingFor(session) {
  if (!session) return "/login";
  return session.role === ROLES.VEHICLE ? "/vehicle" : "/";
}

export function canAccess(session, path) {
  if (!session) return false;
  if (path === "/vehicle") return true; // both roles may view the console
  return session.role === ROLES.ADMIN;
}
