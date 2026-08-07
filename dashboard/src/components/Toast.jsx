import { motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";

const ICONS = {
  success: { Icon: CheckCircle2, colour: "var(--emerald)" },
  error: { Icon: AlertTriangle, colour: "var(--rose)" },
  info: { Icon: Info, colour: "var(--cyan)" },
};

/**
 * Rendered conditionally rather than through AnimatePresence: under React 19
 * the exit animation never resolves, which would leave the toast on screen
 * permanently after the auto-dismiss timer clears it.
 */
export default function Toast({ toast, onClose }) {
  if (!toast) return null;

  const { Icon, colour } = ICONS[toast.kind] || ICONS.info;

  return (
    <motion.div
      key={toast.id}
      className="toast"
      role="status"
      aria-live="polite"
      initial={{ opacity: 0, y: 18, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
    >
      <Icon size={16} style={{ color: colour, flex: "none" }} aria-hidden="true" />
      <span style={{ flex: 1 }}>{toast.message}</span>
      <button
        type="button"
        className="close"
        onClick={onClose}
        title="Dismiss"
        aria-label="Dismiss notification"
      >
        <X size={14} />
      </button>
    </motion.div>
  );
}
