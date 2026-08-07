import { Moon, Sun } from "lucide-react";

import LoginForm from "../components/login/LoginForm.jsx";
import LoginVisual from "../components/login/LoginVisual.jsx";

export default function LoginPage({ onSignIn, theme, onToggleTheme }) {
  return (
    <main className="login">
      <button
        type="button"
        className="login-theme-toggle icon-btn"
        onClick={onToggleTheme}
        title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      >
        {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
      </button>
      <LoginVisual />
      <LoginForm onSignIn={onSignIn} />
    </main>
  );
}
