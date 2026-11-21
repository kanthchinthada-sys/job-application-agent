"""Timing for one preparation: per stage, and per AI call (latency, tokens, retries).

Usage:
    with metrics.collect() as m:           # one run (resume, letter, or both)
        with metrics.stage("analysis"):
            ...
        llm.generate_json(...)              # the Ollama client records each call by itself
    m.report()                               # stages, calls, totals; saved in the document's review

Nothing here changes what is generated.
"""

from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager

_current: contextvars.ContextVar["Collector | None"] = contextvars.ContextVar("metrics", default=None)


class Collector:
    def __init__(self):
        self.started = time.perf_counter()
        self.stages: list[dict] = []
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def add_stage(self, name: str, seconds: float) -> None:
        with self._lock:
            self.stages.append({"stage": name, "seconds": round(seconds, 3)})

    def add_call(self, **info) -> None:
        with self._lock:
            self.calls.append(info)

    def report(self) -> dict:
        total = time.perf_counter() - self.started
        by_stage: dict[str, float] = {}
        for s in self.stages:
            by_stage[s["stage"]] = round(by_stage.get(s["stage"], 0.0) + s["seconds"], 3)
        return {
            "total_seconds": round(total, 3),
            "stages": by_stage,
            "slowest_stage": max(by_stage, key=by_stage.get) if by_stage else None,
            "model_calls": len(self.calls),
            "retries": sum(c.get("retries", 0) for c in self.calls),
            "prompt_chars": sum(c.get("prompt_chars", 0) for c in self.calls),
            "input_tokens": sum(c.get("input_tokens") or 0 for c in self.calls) or None,
            "output_tokens": sum(c.get("output_tokens") or 0 for c in self.calls) or None,
            "model_seconds": round(sum(c.get("seconds", 0.0) for c in self.calls), 3),
            "calls": self.calls,
        }


@contextmanager
def collect(new: bool = False):
    """Start a run (or join the one already running in this context, unless new=True)."""
    existing = _current.get()
    if existing is not None and not new:
        yield existing
        return
    c = Collector()
    token = _current.set(c)
    try:
        yield c
    finally:
        _current.reset(token)


def current() -> Collector | None:
    return _current.get()


@contextmanager
def stage(name: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        c = _current.get()
        if c is not None:
            c.add_stage(name, time.perf_counter() - start)


def record_call(purpose: str, seconds: float, prompt_chars: int, input_tokens=None, output_tokens=None,
                retries: int = 0, load_seconds=None, queue_seconds: float = 0.0) -> None:
    c = _current.get()
    if c is not None:
        c.add_call(purpose=purpose, seconds=round(seconds - queue_seconds, 3), prompt_chars=prompt_chars,
                   input_tokens=input_tokens, output_tokens=output_tokens, retries=retries,
                   load_seconds=load_seconds, queue_seconds=round(queue_seconds, 3))


_usage: contextvars.ContextVar["dict | None"] = contextvars.ContextVar("usage", default=None)


def note_usage(**usage) -> None:
    """Called by the model client with what the server reported (tokens, load time)."""
    slot = _usage.get()
    if slot is not None:
        slot.update(usage)


def call(llm, purpose: str, system: str, user: str, schema: dict) -> dict:
    """One model call, timed and recorded (purpose, latency, prompt size, tokens when the server reports them)."""
    slot: dict = {}
    token = _usage.set(slot)
    start = time.perf_counter()
    try:
        return llm.generate_json(system, user, schema)
    finally:
        _usage.reset(token)
        record_call(purpose, time.perf_counter() - start, len(system) + len(user),
                    input_tokens=slot.get("input_tokens"), output_tokens=slot.get("output_tokens"),
                    retries=slot.get("retries", 0), load_seconds=slot.get("load_seconds"),
                    queue_seconds=slot.get("queue_seconds", 0.0))


class Lap:
    """Mark the end of each stage in a long function: lap("analysis"); ...; lap("page fitting")."""

    def __init__(self, prefix: str = ""):
        self.prefix, self.last = prefix, time.perf_counter()

    def __call__(self, name: str) -> None:
        now = time.perf_counter()
        c = _current.get()
        if c is not None:
            c.add_stage(f"{self.prefix}{name}", now - self.last)
        self.last = now
