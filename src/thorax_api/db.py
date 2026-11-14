"""Shared SQLite access.

Schema and queries stay portable (no SQLite-only types, defaults or functions) so moving
to PostgreSQL on AWS RDS later means changing this module, not the stores. The one
dialect detail left is the DB-API placeholder: sqlite3 uses `?`, psycopg uses `%s`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@contextmanager
def connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    # sqlite3's own context manager only commits; close explicitly so handles do not leak.
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()
