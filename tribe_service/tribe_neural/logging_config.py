"""Structured logging for the TRIBE service.

Reads `LOG_LEVEL` from env (default INFO). Exposes `get_logger(name)` which
returns a logger that emits JSON-friendly records with a stable set of
`extra` fields used across the pipeline:

    run_id   — propagates from the Aesthesis app through to the worker
    version  — "A" or "B" — which video this log line is about
    step     — pipeline phase (`tribe`, `roi`, `composites`, `timeline`, ...)
    elapsed_ms — wall time of the step that just finished

Usage:
    log = get_logger(__name__)
    log.info("ROI extracted", extra={"step": "roi", "n_trs": 42})

The formatter is plain text by default (greppable). Set `LOG_FORMAT=json`
for one-line JSON records (Modal/Datadog/etc. ingest these cleanly).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

DEFAULT_FIELDS = ("run_id", "version", "step", "elapsed_ms", "n_trs", "shape")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in DEFAULT_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        # Allow ad-hoc extra fields too. We can't introspect kwargs cleanly,
        # so fall back to the LogRecord's __dict__ minus the standard noise.
        for key, value in record.__dict__.items():
            if key in payload or key in _STD_LOGRECORD_KEYS:
                continue
            if key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_STD_LOGRECORD_KEYS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime",
}


def _make_text_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s [%(levelname)-5s] %(name)s :: %(message)s",
        datefmt="%H:%M:%S",
    )


_CONFIGURED = False


def configure_logging() -> None:
    """Idempotent root-logger setup. Safe to call from any entry point
    (uvicorn, ARQ worker, Modal stub, pytest)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = os.getenv("LOG_FORMAT", "text").lower()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_JsonFormatter() if fmt == "json" else _make_text_formatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet a few noisy upstream loggers unless the user explicitly turned
    # debug on — these are not signal at INFO.
    if level > logging.DEBUG:
        for noisy in ("urllib3", "httpcore", "asyncio", "matplotlib"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


@contextmanager
def timed_step(log: logging.Logger, step: str, **extra: Any) -> Iterator[dict[str, Any]]:
    """Context manager that logs entry / exit for a pipeline step with a wall
    clock measurement. Yields a mutable dict the body can stuff extra
    measurements into (e.g., shape, n_trs)."""
    payload: dict[str, Any] = {"step": step, **extra}
    log.info("%s start", step, extra=payload)
    t0 = time.perf_counter()
    try:
        yield payload
    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        log.exception("%s failed", step, extra={**payload, "elapsed_ms": round(elapsed_ms, 2)})
        raise
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    log.info(
        "%s done",
        step,
        extra={**payload, "elapsed_ms": round(elapsed_ms, 2)},
    )
