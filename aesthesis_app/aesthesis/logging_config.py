"""Aesthesis app logging — mirrors tribe_neural.logging_config.

Same fields, same formatters. Importing app code never imports
tribe_neural code (the two services are deployed independently), but the
log line shape is intentionally identical so a log aggregator can stitch
runs across services with `run_id`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

DEFAULT_FIELDS = ("run_id", "version", "step", "elapsed_ms", "status_code")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for f in DEFAULT_FIELDS:
            if hasattr(record, f):
                payload[f] = getattr(record, f)
        for k, v in record.__dict__.items():
            if k in payload or k in _STD or k.startswith("_"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_STD = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime",
}


_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    fmt = os.getenv("LOG_FORMAT", "text").lower()

    handler = logging.StreamHandler(stream=sys.stdout)
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s [%(levelname)-5s] %(name)s :: %(message)s",
            datefmt="%H:%M:%S",
        ))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    if level > logging.DEBUG:
        for noisy in ("urllib3", "httpcore", "httpx", "asyncio", "multipart"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


@contextmanager
def timed_step(log: logging.Logger, step: str, **extra: Any) -> Iterator[dict[str, Any]]:
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
    log.info("%s done", step, extra={**payload, "elapsed_ms": round(elapsed_ms, 2)})
