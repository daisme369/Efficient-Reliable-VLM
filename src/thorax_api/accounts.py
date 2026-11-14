"""Account store: SQLite users with scrypt password hashes, plus a small admin CLI.

Passwords never come from the command line or the repository: the CLI reads them
from an environment variable or an interactive prompt.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import connect, utc_now

ROLES = ("doctor", "admin", "annotator")
# OWASP password-storage minimum for scrypt: N=2^17, r=8, p=1 (about 128 MiB per hash).
SCRYPT_N, SCRYPT_R, SCRYPT_P, KEY_BYTES = 2**17, 8, 1, 64
SCRYPT_MAXMEM = 256 * 1024 * 1024
PASSWORD_MIN_BYTES, PASSWORD_MAX_BYTES = 12, 72


@dataclass(frozen=True)
class User:
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    password_hash: str

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "email": self.email, "role": self.role, "is_active": self.is_active}


def valid_password(password: str, minimum: int = PASSWORD_MIN_BYTES) -> bool:
    size = len(password.encode("utf-8"))
    return minimum <= size <= PASSWORD_MAX_BYTES


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=SCRYPT_MAXMEM)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


# Verifying against this when the email is unknown keeps response time independent of account existence.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def password_matches(password: str, stored: str | None) -> bool:
    scheme, n, r, p, salt, digest = (stored or _DUMMY_HASH).split("$")
    if scheme != "scrypt":
        return False
    candidate = _derive(password, bytes.fromhex(salt), int(n), int(r), int(p))
    return hmac.compare_digest(candidate, bytes.fromhex(digest)) and stored is not None


USERS_TABLE = """CREATE TABLE {name} (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('doctor', 'admin', 'annotator')),
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
)"""


class AccountStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as db:
            existing = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
            if existing is None:
                db.execute(USERS_TABLE.format(name="users"))
        if existing is not None and "'annotator'" not in existing[0]:
            self._add_annotator_role()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS auth_events (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users (id),
                succeeded INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )""")

    def record_sign_in(self, user_id: int, succeeded: bool) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO auth_events (user_id, succeeded, created_at) VALUES (?, ?, ?)", (user_id, int(succeeded), utc_now()))

    def all_users(self) -> list[dict]:
        """Every account with its last successful sign-in and failed attempts in the past 24 hours. No password hashes."""
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self._connect() as db:
            rows = db.execute(
                """SELECT u.id, u.name, u.email, u.role, u.is_active, u.created_at,
                          (SELECT MAX(created_at) FROM auth_events e WHERE e.user_id = u.id AND e.succeeded = 1) AS last_sign_in_at,
                          (SELECT COUNT(*) FROM auth_events e WHERE e.user_id = u.id AND e.succeeded = 0 AND e.created_at >= ?) AS failed_sign_ins_24h
                   FROM users u ORDER BY u.role, u.id""", (since,)).fetchall()
        return [{**dict(row), "is_active": bool(row["is_active"])} for row in rows]

    def _add_annotator_role(self) -> None:
        """SQLite cannot alter a CHECK constraint: rebuild the table (its documented 12-step procedure)."""
        db = sqlite3.connect(self.path)
        try:
            db.execute("PRAGMA foreign_keys = OFF")  # other tables reference users(id); keep them intact
            with db:
                db.execute(USERS_TABLE.format(name="users_migrating"))
                db.execute("INSERT INTO users_migrating SELECT id, email, name, role, password_hash, is_active, created_at FROM users")
                db.execute("DROP TABLE users")
                db.execute("ALTER TABLE users_migrating RENAME TO users")
                if db.execute("PRAGMA foreign_key_check").fetchall():
                    raise sqlite3.IntegrityError("user migration broke a foreign key")
        finally:
            db.close()

    def _connect(self):
        return connect(self.path)

    @staticmethod
    def _user(row: sqlite3.Row | None) -> User | None:
        if row is None:
            return None
        return User(row["id"], row["name"], row["email"], row["role"], bool(row["is_active"]), row["password_hash"])

    def by_email(self, email: str) -> User | None:
        with self._connect() as db:
            return self._user(db.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone())

    def by_id(self, user_id: int) -> User | None:
        with self._connect() as db:
            return self._user(db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    def create(self, email: str, name: str, role: str, password: str, user_id: int | None = None) -> User:
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        if not valid_password(password):
            raise ValueError(f"password must be {PASSWORD_MIN_BYTES}-{PASSWORD_MAX_BYTES} UTF-8 bytes")
        email = email.strip().lower()
        if not email or "@" not in email or len(email) > 254:
            raise ValueError("email is not valid")
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO users (id, email, name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, email, name.strip(), role, hash_password(password), utc_now()),
            )
            created = cursor.lastrowid
        return self.by_id(created)

    def set_active(self, email: str, active: bool) -> bool:
        with self._connect() as db:
            return db.execute("UPDATE users SET is_active = ? WHERE email = ?", (int(active), email.strip().lower())).rowcount == 1

    def set_password(self, email: str, password: str) -> bool:
        if not valid_password(password):
            raise ValueError(f"password must be {PASSWORD_MIN_BYTES}-{PASSWORD_MAX_BYTES} UTF-8 bytes")
        with self._connect() as db:
            return db.execute("UPDATE users SET password_hash = ? WHERE email = ?", (hash_password(password), email.strip().lower())).rowcount == 1


# Same synthetic accounts and IDs as the web demo fixture, so assignments line up later.
DEMO_ACCOUNTS = (
    (1, "Dr. Maya Bennett", "doctor@thorax.demo", "doctor", "THORAX_DEMO_DOCTOR_PASSWORD"),
    (2, "Dr. Leo Martinez", "doctor2@thorax.demo", "doctor", "THORAX_DEMO_DOCTOR_PASSWORD"),
    (3, "Demo administrator", "admin@thorax.demo", "admin", "THORAX_DEMO_ADMIN_PASSWORD"),
    (4, "Ana Silva", "annotator@thorax.demo", "annotator", "THORAX_DEMO_ANNOTATOR_PASSWORD"),
    (5, "Ben Carter", "annotator2@thorax.demo", "annotator", "THORAX_DEMO_ANNOTATOR_PASSWORD"),
)


def _password(env_name: str) -> str:
    value = os.environ.get(env_name)
    return value if value is not None else getpass.getpass("Password: ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m thorax_api.accounts", description="Manage Thorax API accounts.")
    parser.add_argument("--db", default=os.environ.get("THORAX_DB_PATH", "thorax.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create an account; password from THORAX_NEW_PASSWORD or a prompt.")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", choices=ROLES, required=True)
    for name in ("disable", "enable", "reset-password"):
        commands.add_parser(name).add_argument("--email", required=True)
    commands.add_parser("seed-demo", help="Create the synthetic demo accounts (THORAX_DEMO_*_PASSWORD) and demo patients/studies/images.")
    args = parser.parse_args(argv)
    store = AccountStore(args.db)
    try:
        if args.command == "create":
            user = store.create(args.email, args.name, args.role, _password("THORAX_NEW_PASSWORD"))
            print(f"created user {user.id} ({user.role})")
        elif args.command in ("disable", "enable"):
            if not store.set_active(args.email, args.command == "enable"):
                parser.error("no account with that email")
            print(f"{args.command}d")
        elif args.command == "reset-password":
            if not store.set_password(args.email, _password("THORAX_NEW_PASSWORD")):
                parser.error("no account with that email")
            print("password reset")
        else:
            from .catalog import Catalog, seed_demo
            from .storage import LocalImageStore

            for user_id, name, email, role, env_name in DEMO_ACCOUNTS:
                password = os.environ.get(env_name)
                if not password and role == "annotator":
                    print(f"skipped {email}: set {env_name} to create the demo annotators")
                    continue
                if not password:
                    parser.error(f"set {env_name} first")
                if store.by_email(email) is None:
                    store.create(email, name, role, password, user_id)
            catalog = Catalog(args.db, LocalImageStore(os.environ.get("THORAX_IMAGE_ROOT", "thorax-images")))
            seed_demo(catalog)
            from .labeling import LabelStore
            with connect(args.db) as db:
                LabelStore(args.db).create_tasks([row[0] for row in db.execute("SELECT id FROM images ORDER BY id")])
            print("demo accounts, patients, studies, images and labeling tasks ready")
    except (ValueError, sqlite3.IntegrityError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
