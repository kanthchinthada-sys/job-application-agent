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
    conn = database.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()
