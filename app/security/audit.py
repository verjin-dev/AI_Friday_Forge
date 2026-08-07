from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.models import SecurityVerdict


logger = get_logger(__name__)


def record_event(
    event: str,
    *,
    trace_id: str,
    role: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append a tamper-evident-ish line to the security audit log.

    Deliberately append-only JSONL so it can be shipped to a SIEM unchanged.
    """

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "trace_id": trace_id,
        "role": role,
        **(detail or {}),
    }
    try:
        settings.security_audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with settings.security_audit_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
    except OSError as exc:
        logger.error("Failed to write security audit log", extra={"error": str(exc)})


def record_verdict(verdict: SecurityVerdict, *, trace_id: str) -> None:
    record_event(
        "security_verdict",
        trace_id=trace_id,
        role=verdict.role,
        detail={
            "allowed": verdict.allowed,
            "max_severity": verdict.max_severity.value,
            "blocked_reason": verdict.blocked_reason,
            "findings": [
                {"check": f.check, "severity": f.severity.value, "detail": f.detail}
                for f in verdict.findings
            ],
        },
    )
