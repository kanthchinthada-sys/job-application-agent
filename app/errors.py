"""Domain errors raised by the repository and translated to HTTP by main.py.

Keeping these independent of FastAPI means the data layer can be reused (by a
CLI, a background worker, or tests) without importing web code.
"""


class NotFoundError(Exception):
    """A requested record does not exist -> HTTP 404."""


class ConflictError(Exception):
    """The request would create a duplicate or break a rule -> HTTP 409."""

    def __init__(self, message: str, existing_id: int | None = None):
        super().__init__(message)
        self.existing_id = existing_id
