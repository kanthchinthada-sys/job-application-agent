"""Shared FastAPI dependencies."""

import sqlite3
from collections.abc import Iterator

from fastapi import Request

from app import database


def get_connection(request: Request) -> Iterator[sqlite3.Connection]:
    """One SQLite connection per request, always closed afterwards.

    FastAPI runs the code before ``yield`` before the endpoint, injects the
    yielded value, and runs the ``finally`` block after the response is built.
    """
    conn = database.connect(db_path(request))
    try:
        yield conn
    finally:
        conn.close()


def db_path(request: Request):
    """The signed-in account's workspace file (accounts on), or the app's single database."""
    return getattr(request.state, "db_path", None) or request.app.state.db_path


def llm(request: Request):
    """The signed-in account's AI: Claude or OpenAI with its own saved key, else the local model."""
    from app.llm import AccountLLM
    path = db_path(request)
    cache = request.app.state.account_llms
    if path not in cache:
        cache[path] = AccountLLM(path, request.app.state.llm)
    return cache[path]


def preparer(request: Request):
    """Background preparation for the signed-in account's workspace (one queue per workspace)."""
    path = getattr(request.state, "db_path", None)
    if path is None:
        return request.app.state.preparer
    from app.preparation import Preparer
    preparers = request.app.state.preparers
    if path not in preparers:
        preparers[path] = Preparer(path, llm(request))
    return preparers[path]


def intake_worker(request: Request):
    """The model's background step after adding a job, for the signed-in account's workspace."""
    path = getattr(request.state, "db_path", None)
    if path is None:
        return request.app.state.intake
    from app.intake_runs import IntakeWorker
    workers = request.app.state.intake_workers
    if path not in workers:
        workers[path] = IntakeWorker(path, llm(request))
    return workers[path]
