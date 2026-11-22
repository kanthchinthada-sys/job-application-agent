"""Logging and request tracing.

* Every request is logged as one line: request id, method, path, status, time taken.
  Query strings and bodies are never logged (they can hold your personal data).
* A redaction filter masks email addresses and phone numbers in ANY log line,
  in case one slips into an error message.
* Logs go to the console and to data/logs/app.log (rotated at 1 MB, 5 files kept).
  data/ is git-ignored, so logs never reach GitHub.
"""

import logging
import re
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request

logger = logging.getLogger("job_agent")

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")


def redact(text: str) -> str:
    text = EMAIL.sub("[email]", text)
    return PHONE.sub("[phone]", text)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def setup_logging(log_dir: Path | None, level: str = "INFO") -> None:
    """Configure the 'job_agent' logger once. ``log_dir=None`` logs to the console only (tests)."""
    logger.setLevel(level)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactFilter())
        logger.addHandler(h)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",     # the app may show its own printable pages (resume preview); no other site may
    "Referrer-Policy": "no-referrer",
}


def add_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            ms = (time.perf_counter() - start) * 1000
            logger.exception("req=%s %s %s -> 500 %.0fms (unhandled error)", request_id, request.method, request.url.path, ms)
            raise
        ms = (time.perf_counter() - start) * 1000
        level = logging.WARNING if response.status_code >= 500 else logging.INFO
        logger.log(level, "req=%s %s %s -> %s %.0fms", request_id, request.method, request.url.path, response.status_code, ms)
        response.headers["X-Request-ID"] = request_id
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path not in ("/", "/login") and not request.url.path.startswith(("/static", "/ui")):
            response.headers.setdefault("Cache-Control", "no-store")   # personal data: don't cache
        return response
