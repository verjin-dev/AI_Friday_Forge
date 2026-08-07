import { useState } from "react";
import { motion } from "framer-motion";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Eye,
  EyeOff,
  Fingerprint,
  Lock,
  Loader2,
  ShieldCheck,
  User,
} from "lucide-react";

import { DEMO_ACCOUNTS, authenticate } from "../../config/demoAuth.js";

export default function LoginForm({ onSignIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [errors, setErrors] = useState({});
  const [state, setState] = useState("idle"); // idle | working | success

  const validate = () => {
    const next = {};
    if (!username.trim()) next.username = "Enter your username.";
    if (!password) next.password = "Enter your password.";
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async (event) => {
    event.preventDefault();
    if (state !== "idle") return;
    if (!validate()) return;

    setState("working");
    // Brief pause so the loading state is perceivable; there is no network
    // call here because this is a front-end demo gate.
    await new Promise((resolve) => setTimeout(resolve, 420));

    const session = authenticate(username, password);
    if (!session) {
      setState("idle");
      setErrors({ form: "Those credentials were not recognised." });
      return;
    }

    setState("success");
    setTimeout(() => onSignIn(session), 380);
  };

  // Not named `useDemo...` because the leading "use" makes lint treat it as a hook.
  const applyDemoAccount = (account) => {
    setUsername(account.username);
    setPassword(account.password);
    setErrors({});
  };

  return (
    <section className="login-panel">
      <motion.form
        className="login-card"
        onSubmit={submit}
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.36, ease: "easeOut" }}
        noValidate
      >
        <span className="login-badge">
          <ShieldCheck size={11} aria-hidden="true" />
          Secure workspace
        </span>

        <h2>Welcome back</h2>
        <p>Sign in to access your logistics intelligence workspace.</p>

        <div className="login-assurance" aria-label="Access controls">
          <span>
            <Fingerprint size={13} aria-hidden="true" />
            Identity verified
          </span>
          <span>
            <ShieldCheck size={13} aria-hidden="true" />
            Role-based access
          </span>
        </div>

        <div className="login-field">
          <label htmlFor="login-username">Email / Username</label>
          <div className={`login-input ${errors.username ? "invalid" : ""}`}>
            <User size={15} aria-hidden="true" style={{ color: "var(--text-faint)" }} />
            <input
              id="login-username"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(event) => {
                setUsername(event.target.value);
                setErrors((current) => ({ ...current, username: null, form: null }));
              }}
              placeholder="Enter username"
              aria-invalid={Boolean(errors.username)}
            />
          </div>
          {errors.username && (
            <p className="field-error">
              <AlertCircle size={12} aria-hidden="true" />
              {errors.username}
            </p>
          )}
        </div>

        <div className="login-field">
          <label htmlFor="login-password">Password</label>
          <div className={`login-input ${errors.password ? "invalid" : ""}`}>
            <Lock size={15} aria-hidden="true" style={{ color: "var(--text-faint)" }} />
            <input
              id="login-password"
              name="password"
              type={reveal ? "text" : "password"}
              autoComplete="current-password"
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
                setErrors((current) => ({ ...current, password: null, form: null }));
              }}
              placeholder="Enter password"
              aria-invalid={Boolean(errors.password)}
            />
            <button
              type="button"
              onClick={() => setReveal((value) => !value)}
              title={reveal ? "Hide password" : "Show password"}
              aria-label={reveal ? "Hide password" : "Show password"}
            >
              {reveal ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>
          {errors.password && (
            <p className="field-error">
              <AlertCircle size={12} aria-hidden="true" />
              {errors.password}
            </p>
          )}
        </div>

        {errors.form && (
          <p className="field-error" role="alert">
            <AlertCircle size={12} aria-hidden="true" />
            {errors.form}
          </p>
        )}

        <button
          type="submit"
          className="pill-btn primary login-submit"
          disabled={state !== "idle"}
        >
          {state === "working" && (
            <>
              <Loader2 size={15} className="spin" aria-hidden="true" />
              Signing in...
            </>
          )}
          {state === "success" && (
            <>
              <CheckCircle2 size={15} aria-hidden="true" />
              Signed in
            </>
          )}
          {state === "idle" && (
            <>
              Sign In
              <ArrowRight size={15} aria-hidden="true" />
            </>
          )}
        </button>

        <div className="demo-accounts">
          <h4>Demo access profiles</h4>
          {DEMO_ACCOUNTS.map((account) => (
            <button
              key={account.username}
              type="button"
              className="demo-account"
              onClick={() => applyDemoAccount(account)}
            >
              <span style={{ flex: 1 }}>
                <code>{account.username}</code> / <code>{account.password}</code>
              </span>
              <span style={{ color: "var(--text-faint)" }}>{account.name}</span>
            </button>
          ))}
        </div>

        <p className="login-trust">
          Secure access to LogiPilot Ai Logistics Control Tower
        </p>
      </motion.form>
    </section>
  );
}
