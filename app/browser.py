"""One headless Chromium, kept open and reused for every page measurement.

Fitting a resume on two pages measures the printed page up to ~10 times. Launching Chromium for each
measurement cost ~0.7 s every time; this worker launches it once and keeps it for later measurements (it
closes itself after a few idle minutes).

Playwright's sync API must stay on the thread that started it, so all browser work runs on this module's own
thread; callers hand it a function and wait for the result.
"""

from __future__ import annotations

import os
import queue
import threading
from concurrent.futures import Future

IDLE_SECONDS = 300

_jobs: "queue.Queue[tuple[object, Future] | None]" = queue.Queue()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _worker() -> None:
    global _thread
    pw = browser = None
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
    except Exception:
        pw = None
    while True:
        try:
            item = _jobs.get(timeout=IDLE_SECONDS)
        except queue.Empty:
            break                                   # idle: close the browser; it starts again when needed
        if item is None:
            break
        fn, fut = item
        if fut.set_running_or_notify_cancel() is False:
            continue
        try:
            if pw is None:
                raise RuntimeError("Playwright is not available")
            if browser is None or not browser.is_connected():
                exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
                browser = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
            fut.set_result(fn(browser))
        except Exception as exc:                    # the caller falls back to its estimate
            fut.set_exception(exc)
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass
            browser = None
    try:
        if browser is not None:
            browser.close()
        if pw is not None:
            pw.stop()
    except Exception:
        pass
    with _lock:
        _thread = None


def run(fn, timeout: float = 120):
    """Run fn(browser) on the browser thread and return its result (raises what fn raised)."""
    global _thread
    fut: Future = Future()
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, name="chromium", daemon=True)
            _thread.start()
        _jobs.put((fn, fut))
    return fut.result(timeout=timeout)


def print_page(browser, html: str):
    """A new page with the resume laid out as printed (US Letter at 96 dpi). Close it when done."""
    page = browser.new_page(viewport={"width": 816, "height": 1056})
    page.set_content(html)
    page.emulate_media(media="print")
    return page
