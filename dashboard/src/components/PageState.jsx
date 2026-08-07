import { AlertTriangle, Loader2 } from "lucide-react";

/** Shared loading / error / empty presentation so pages behave consistently. */
export default function PageState({ loading, error, empty, emptyText, children }) {
  if (loading) {
    return (
      <p className="empty-state">
        <Loader2 size={15} className="spin" aria-hidden="true" /> Loading…
      </p>
    );
  }

  if (error) {
    return (
      <p className="empty-state" style={{ color: "var(--rose)" }}>
        <AlertTriangle size={15} aria-hidden="true" /> {error}
      </p>
    );
  }

  if (empty) {
    return <p className="empty-state">{emptyText || "Nothing to show."}</p>;
  }

  return children;
}
