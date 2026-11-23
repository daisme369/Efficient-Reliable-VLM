"""Copy a Thorax SQLite database into an empty PostgreSQL database (the one-time move from Lightsail to RDS).

    python -m thorax_api.migrate --source /data/import/thorax.sqlite3      # target: THORAX_DATABASE_URL

Creates the schema by starting the stores on the target, refuses a target that already has accounts, copies every
table in foreign-key order with the same ids, then compares row counts. Image files are copied separately
(they stay under THORAX_IMAGE_ROOT with the same storage keys).
"""

from __future__ import annotations

import argparse
import os
import sqlite3

from .db import columns, connect, is_postgres


def table_order(source: sqlite3.Connection) -> list[str]:
    """Tables with every referenced table before the tables that reference it."""
    tables = [row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    parents = {t: {row[2] for row in source.execute(f"PRAGMA foreign_key_list({t})")} - {t} for t in tables}
    ordered: list[str] = []
    while len(ordered) < len(tables):
        ready = [t for t in tables if t not in ordered and parents[t] <= set(ordered)]
        if not ready:
            raise RuntimeError("circular foreign keys between tables: " + ", ".join(t for t in tables if t not in ordered))
        ordered.extend(ready)
    return ordered


def migrate(source_path: str, target: str) -> dict[str, int]:
    if not is_postgres(target):
        raise ValueError("The target must be a postgresql:// URL.")
    from .app import create_app
    create_app(target, [], image_root=os.environ.get("THORAX_IMAGE_ROOT", "thorax-images"))  # creates every table
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        order = table_order(source)
        with connect(target) as db:
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                raise ValueError("The target already has accounts; migrate only into a new, empty database.")
            for table in reversed(order):  # rows the stores created at start-up (signing secret, id sequence)
                db.execute(f"DELETE FROM {table}")
            counts = {}
            for table in order:
                shared = [c for c in (row[1] for row in source.execute(f"PRAGMA table_info({table})")) if c in columns(db, table, target)]
                shared.sort(key=lambda c: c != "id")  # id first, so db.py moves the identity sequence past copied ids
                rows = source.execute(f"SELECT {', '.join(shared)} FROM {table}").fetchall()
                db.executemany(f"INSERT INTO {table} ({', '.join(shared)}) VALUES ({', '.join('?' * len(shared))})", rows)
                counts[table] = len(rows)
            for table, expected in counts.items():
                copied = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if copied != expected:
                    raise RuntimeError(f"{table}: copied {copied} of {expected} rows")  # rolls the whole copy back
        return counts
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy a Thorax SQLite database into an empty PostgreSQL database.")
    parser.add_argument("--source", required=True, help="SQLite file to copy (opened read-only)")
    parser.add_argument("--target", default=os.environ.get("THORAX_DATABASE_URL"), help="postgresql:// URL (default THORAX_DATABASE_URL)")
    args = parser.parse_args()
    if not args.target:
        parser.error("set --target or THORAX_DATABASE_URL")
    try:
        counts = migrate(args.source, args.target)
    except (ValueError, RuntimeError) as problem:
        parser.error(str(problem))
    for table, count in counts.items():
        print(f"{table:24} {count:6} rows")
    print("copied and verified")


if __name__ == "__main__":
    main()
