"""Database for API tests: a SQLite file by default, or a fresh PostgreSQL schema when THORAX_TEST_DATABASE_URL is set.

Run the suite on PostgreSQL (same major version as RDS):
    THORAX_TEST_DATABASE_URL=postgresql://thorax:thorax@127.0.0.1:55432/thorax python -m unittest discover -s tests/api -t .
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

URL = os.environ.get("THORAX_TEST_DATABASE_URL")


def fresh_db(directory: str | Path, name: str = "thorax.sqlite3") -> str | Path:
    if not URL:
        return Path(directory) / name
    import psycopg
    schema = "t" + uuid.uuid4().hex[:16]  # one empty schema per test; the throwaway test server holds them all
    with psycopg.connect(URL, autocommit=True) as connection:
        connection.execute(f"CREATE SCHEMA {schema}")
    return f"{URL}{'&' if '?' in URL else '?'}options=-csearch_path%3D{schema}"
