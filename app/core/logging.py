from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any


#: Correlation id for the in-flight request, surfaced on every log line.
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

_CONFIGURED = False

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "stacklevel", "thread", "threadName",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line so logs ship straight into any collector."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": trace_id_var.get(),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = _safe(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return str(value)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # These are chatty and add nothing on top of our own agent traces.
    for noisy in ("httpx", "httpcore", "neo4j", "urllib3", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


class SafeLogger(logging.Logger):
    """Logger that tolerates ``extra`` keys colliding with LogRecord internals.

    ``logger.info(..., extra={"name": x})`` raises ``KeyError`` in the stdlib
    because ``name`` is a reserved LogRecord attribute. That turns a harmless
    log line into a 500, which is a poor trade — so colliding keys are renamed
    rather than allowed to raise.
    """

    def makeRecord(self, name, level, fn, lno, msg, args, exc_info, func=None,
                   extra=None, sinfo=None):  # noqa: D102 - stdlib signature
        if extra:
            safe = {
                (f"ctx_{key}" if key in _RESERVED or key in ("message", "asctime")
                 else key): value
                for key, value in extra.items()
            }
        else:
            safe = extra
        return super().makeRecord(
            name, level, fn, lno, msg, args, exc_info, func, safe, sinfo
        )


logging.setLoggerClass(SafeLogger)


def get_logger(name: str) -> logging.LoggerAdapter | logging.Logger:
    if not _CONFIGURED:
        # Lazy import keeps this module importable before settings resolve.
        from app.core.config import settings

        configure_logging(settings.log_level)
    return logging.getLogger(name)


def set_trace_id(trace_id: str) -> None:
    trace_id_var.set(trace_id)
