"""Sign-in sessions and the server signing secret, stored in SQLite so a restart keeps users signed in.

Only sha256(token) is stored; the token itself lives only in the browser's HttpOnly cookie.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets

from .db import connect

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS auth_sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL UNIQUE REFERENCES users (id),
        idle_expires REAL NOT NULL,
        absolute_expires REAL NOT NULL
    )""",
    # ponytail: the secret sits next to the data it protects; move it to a secrets manager when deploying to AWS.
    "CREATE TABLE IF NOT EXISTS server_secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL)",
)


@dataclass
class Login:
    user_id: int
    idle_expires: float
    absolute_expires: float

    def expires(self) -> float:
        return min(self.idle_expires, self.absolute_expires)


class SessionStore:
    def __init__(self, path: str | Path):
        self.path = path
        with connect(path) as db:
            for statement in SCHEMA:
                db.execute(statement)

    def secret(self) -> bytes:
        """Created once, so CSRF tokens and page cursors stay valid across restarts."""
        with connect(self.path) as db:
            db.execute("INSERT OR IGNORE INTO server_secrets (name, value) VALUES ('signing', ?)", (secrets.token_hex(32),))
            return bytes.fromhex(db.execute("SELECT value FROM server_secrets WHERE name = 'signing'").fetchone()[0])

    def get(self, token_hash: str) -> Login | None:
        with connect(self.path) as db:
            row = db.execute("SELECT user_id, idle_expires, absolute_expires FROM auth_sessions WHERE token_hash = ?", (token_hash,)).fetchone()
        return Login(*row) if row else None

    def start(self, token_hash: str, login: Login, now: float) -> None:
        """One active login per account: a new sign-in replaces the previous one. Also drops expired sessions."""
        with connect(self.path) as db:
            db.execute("DELETE FROM auth_sessions WHERE user_id = ? OR idle_expires <= ? OR absolute_expires <= ?", (login.user_id, now, now))
            db.execute("INSERT INTO auth_sessions (token_hash, user_id, idle_expires, absolute_expires) VALUES (?, ?, ?, ?)",
                       (token_hash, login.user_id, login.idle_expires, login.absolute_expires))

    def touch(self, token_hash: str, idle_expires: float) -> None:
        with connect(self.path) as db:
            db.execute("UPDATE auth_sessions SET idle_expires = ? WHERE token_hash = ?", (idle_expires, token_hash))

    def revoke(self, token_hash: str) -> None:
        with connect(self.path) as db:
            db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))

    def revoke_user(self, user_id: int) -> None:
        with connect(self.path) as db:
            db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))

    def signed_in(self, now: float) -> set[int]:
        with connect(self.path) as db:
            rows = db.execute("SELECT user_id FROM auth_sessions WHERE idle_expires > ? AND absolute_expires > ?", (now, now)).fetchall()
        return {row[0] for row in rows}
