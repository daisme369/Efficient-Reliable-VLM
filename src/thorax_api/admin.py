"""Admin API: overview, users of every role, assignments, AI monitoring with a pause switch,
labeling oversight, and a log of admin actions. Every route is admin-only.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .accounts import ROLES, AccountStore
from .catalog import Catalog
from .db import connect, utc_now
from .labeling import LabelStore

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS settings (
        name TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_by INTEGER REFERENCES users (id),
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS admin_events (
        id INTEGER PRIMARY KEY,
        admin_id INTEGER NOT NULL REFERENCES users (id),
        action TEXT NOT NULL,
        detail TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    # Every AI request the backend runs is recorded here (AdminStore.record_ai_request) for monitoring.
    """CREATE TABLE IF NOT EXISTS ai_requests (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users (id),
        operation TEXT NOT NULL,
        status TEXT NOT NULL,
        model_version TEXT,
        error_code TEXT,
        latency_ms INTEGER,
        created_at TEXT NOT NULL
    )""",
)


class AdminStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with connect(self.path) as db:
            for statement in SCHEMA:
                db.execute(statement)

    def log(self, admin_id: int, action: str, detail: str) -> None:
        with connect(self.path) as db:
            db.execute("INSERT INTO admin_events (admin_id, action, detail, created_at) VALUES (?, ?, ?, ?)", (admin_id, action, detail, utc_now()))

    def events(self, limit: int = 100) -> list[dict]:
        with connect(self.path) as db:
            rows = db.execute("""SELECT e.id, e.action, e.detail, e.created_at, u.name AS admin_name FROM admin_events e
                                 JOIN users u ON u.id = e.admin_id ORDER BY e.id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(row) for row in rows]

    # The AI submit path must call ai_paused() before running a request and refuse while it is true.
    def ai_pause(self) -> dict:
        with connect(self.path) as db:
            row = db.execute("""SELECT s.value, s.updated_at, u.name FROM settings s LEFT JOIN users u ON u.id = s.updated_by
                                WHERE s.name = 'ai_paused'""").fetchone()
        return {"paused": bool(row and row["value"] == "1"), "changed_at": row["updated_at"] if row else None, "changed_by": row["name"] if row else None}

    def ai_paused(self) -> bool:
        return self.ai_pause()["paused"]

    def set_ai_paused(self, paused: bool, admin_id: int) -> None:
        with connect(self.path) as db:
            db.execute("DELETE FROM settings WHERE name = 'ai_paused'")
            db.execute("INSERT INTO settings (name, value, updated_by, updated_at) VALUES ('ai_paused', ?, ?, ?)", (str(int(paused)), admin_id, utc_now()))
        self.log(admin_id, "ai_paused" if paused else "ai_resumed", "AI requests paused" if paused else "AI requests resumed")

    def record_ai_request(self, user_id: int, operation: str, status: str, *, model_version: str | None = None,
                          error_code: str | None = None, latency_ms: int | None = None, db=None) -> None:
        """Pass `db` to write inside the caller's open transaction (SQLite allows one writer at a time)."""
        values = (user_id, operation, status, model_version, error_code, latency_ms, utc_now())
        sql = "INSERT INTO ai_requests (user_id, operation, status, model_version, error_code, latency_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
        if db is not None:
            db.execute(sql, values)
            return
        with connect(self.path) as own:
            own.execute(sql, values)

    def ai_requests(self, limit: int = 100) -> dict:
        with connect(self.path) as db:
            rows = [dict(row) for row in db.execute(
                """SELECT r.id, r.operation, r.status, r.model_version, r.error_code, r.latency_ms, r.created_at, u.name AS user_name
                   FROM ai_requests r JOIN users u ON u.id = r.user_id ORDER BY r.id DESC LIMIT ?""", (limit,))]
            total = db.execute("SELECT COUNT(*) FROM ai_requests").fetchone()[0]
        done = [row["latency_ms"] for row in rows if row["status"] == "succeeded" and row["latency_ms"] is not None]
        return {"items": rows, "stats": {
            "total": total, "shown": len(rows),
            "failed": sum(row["status"] == "failed" for row in rows), "timed_out": sum(row["status"] == "timed_out" for row in rows),
            "average_ms": round(sum(done) / len(done)) if done else None, "slowest_ms": max(done) if done else None,
        }}


def model_service() -> dict:
    """Health of the separate inference service (scripts/serve_vlm.py), if one is configured."""
    url = os.environ.get("THORAX_VLM_URL", "").rstrip("/")
    if not url:
        return {"status": "not_configured", "url": None}
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=2) as response:  # noqa: S310 - admin-configured URL
            return {"status": "up" if response.status == 200 else "down", "url": url}
    except OSError:
        return {"status": "down", "url": url}


def register_admin(app: FastAPI, *, accounts: AccountStore, catalog: Catalog, labels: LabelStore, started_at: str,
                   actor, page, read_body, no_store, invalid, error, path_id, sessions) -> AdminStore:
    """`sessions` exposes signed_in() -> set of user ids and revoke(user_id) from the auth state."""
    store = AdminStore(accounts.path)
    ok = lambda data, status=200: no_store(JSONResponse(data, status_code=status))  # noqa: E731
    admin = lambda request: actor(request, "admin")  # noqa: E731

    async def body_of(request: Request, allowed: set[str]) -> dict:
        body = await read_body(request)
        if set(body) - allowed:
            raise invalid("Unexpected request field.")
        return body

    def user_or_404(user_id: str):
        user = accounts.by_id(path_id(user_id))
        if user is None:
            raise error(404, "RESOURCE_NOT_FOUND", "This resource is unavailable.")
        return user

    @app.get("/api/v1/admin/overview")
    async def overview(request: Request):
        admin(request)
        users = accounts.all_users()
        signed_in = sessions.signed_in()
        ai = store.ai_requests(200)["stats"]
        return ok({
            "users": {role: {"active": sum(u["role"] == role and u["is_active"] for u in users), "total": sum(u["role"] == role for u in users)} for role in ROLES},
            "signed_in": len(signed_in & {u["id"] for u in users}),
            "catalog": catalog.counts(),
            "labeling": labels.queue(0)["counts"],
            "ai": {**store.ai_pause(), "requests": ai["total"], "failed": ai["failed"] + ai["timed_out"], "model_service": await run_in_threadpool(model_service)},
            "backend": {"status": "ok", "started_at": started_at},
        })

    @app.get("/api/v1/admin/users")
    async def users(request: Request):
        admin(request)
        signed_in = sessions.signed_in()
        return ok({"items": [{**user, "signed_in": user["id"] in signed_in} for user in accounts.all_users()]})

    @app.post("/api/v1/admin/users")
    async def create_user(request: Request):
        me = admin(request)
        body = await body_of(request, {"name", "email", "role", "password"})
        if not all(isinstance(body.get(key), str) and body[key].strip() for key in ("name", "email", "role", "password")) or len(body["name"]) > 100:
            raise invalid("Enter a name, email, role and initial password.")
        try:
            user = accounts.create(body["email"], body["name"], body["role"], body["password"])
        except ValueError as problem:
            raise invalid(str(problem).capitalize() + ".") from None
        except sqlite3.IntegrityError:
            raise error(409, "EMAIL_TAKEN", "An account with that email already exists.") from None
        store.log(me.id, "user_created", f"{user.role} {user.email}")
        return ok(user.public(), 201)

    @app.patch("/api/v1/admin/users/{user_id}")
    async def change_user(request: Request, user_id: str):
        me = admin(request)
        user = user_or_404(user_id)
        body = await body_of(request, {"is_active", "password"})
        if not body:
            raise invalid("Send is_active or password.")
        if "is_active" in body:
            if not isinstance(body["is_active"], bool):
                raise invalid("is_active must be true or false.")
            if user.id == me.id and not body["is_active"]:
                raise error(409, "SELF_LOCKOUT", "You can’t disable your own account.")
            accounts.set_active(user.email, body["is_active"])
            if not body["is_active"]:
                sessions.revoke(user.id)
            store.log(me.id, "user_enabled" if body["is_active"] else "user_disabled", user.email)
        if "password" in body:
            try:
                accounts.set_password(user.email, body["password"] if isinstance(body["password"], str) else "")
            except ValueError as problem:
                raise invalid(str(problem).capitalize() + ".") from None
            if user.id != me.id:
                sessions.revoke(user.id)  # the old password's sessions end with it
            store.log(me.id, "password_reset", user.email)
        return ok(accounts.by_id(user.id).public())

    @app.post("/api/v1/admin/users/{user_id}/sign-out")
    async def sign_out_user(request: Request, user_id: str):
        me = admin(request)
        user = user_or_404(user_id)
        await body_of(request, set())
        sessions.revoke(user.id)
        store.log(me.id, "forced_sign_out", user.email)
        return no_store(Response(status_code=204))

    # --- assignments: the contract the existing Assignments tab already uses ---
    @app.get("/api/v1/admin/doctors")
    async def doctors(request: Request):
        me = admin(request)
        assigned = catalog.assignments()
        rows = [{"id": u["id"], "name": u["name"], "email": u["email"], "role": "doctor", "is_active": u["is_active"],
                 "assigned_patient_ids": sorted(p for d, p in assigned if d == u["id"])} for u in accounts.all_users() if u["role"] == "doctor"]
        return page(request, me, rows)

    @app.get("/api/v1/admin/patient-catalog")
    async def patient_catalog(request: Request):
        me = admin(request)
        search = (request.query_params.get("search") or "").strip().lower()
        assigned = catalog.assignments()
        rows = [{"id": pid, "code": code, "assigned_doctor_ids": sorted(d for d, p in assigned if p == pid)}
                for pid, code in catalog.patient_codes() if search in code.lower()]
        return page(request, me, rows)

    @app.post("/api/v1/admin/assignments")
    async def assign(request: Request):
        me = admin(request)
        body = await body_of(request, {"doctor_id", "patient_id"})
        doctor = accounts.by_id(body["doctor_id"]) if isinstance(body.get("doctor_id"), int) else None
        code = dict(catalog.patient_codes()).get(body.get("patient_id")) if isinstance(body.get("patient_id"), int) else None
        if doctor is None or doctor.role != "doctor" or code is None:
            raise error(404, "RESOURCE_NOT_FOUND", "This resource is unavailable.")
        if not doctor.is_active:
            raise error(409, "DOCTOR_INACTIVE", "Enable the doctor before granting access.")
        catalog.set_assignment(doctor.id, body["patient_id"], True)
        store.log(me.id, "access_granted", f"{doctor.email} → {code}")
        return ok({"resource_type": "assignment", "resource_id": f"{doctor.id}:{body['patient_id']}"}, 201)

    @app.delete("/api/v1/admin/assignments/{doctor_id}/{patient_id}")
    async def unassign(request: Request, doctor_id: str, patient_id: str):
        me = admin(request)
        doctor, pid = user_or_404(doctor_id), path_id(patient_id)
        if catalog.set_assignment(doctor.id, pid, False):
            store.log(me.id, "access_revoked", f"{doctor.email} → {dict(catalog.patient_codes()).get(pid, pid)}")
        return no_store(Response(status_code=204))

    # --- AI monitoring and the pause switch ---
    @app.get("/api/v1/admin/ai")
    async def ai(request: Request):
        admin(request)
        return ok({**store.ai_requests(), "pause": store.ai_pause(), "model_service": await run_in_threadpool(model_service)})

    @app.put("/api/v1/admin/ai/pause")
    async def pause(request: Request):
        me = admin(request)
        body = await body_of(request, {"paused"})
        if not isinstance(body.get("paused"), bool):
            raise invalid("paused must be true or false.")
        store.set_ai_paused(body["paused"], me.id)
        return ok(store.ai_pause())

    # --- labeling oversight ---
    @app.get("/api/v1/admin/labeling")
    async def labeling(request: Request):
        admin(request)
        return ok(labels.oversight())

    @app.post("/api/v1/admin/labeling/export")
    async def export_labels(request: Request):
        me = admin(request)
        await body_of(request, set())
        with tempfile.TemporaryDirectory() as directory:
            manifest = labels.export(directory)
            text = (Path(directory) / "labels.jsonl").read_text(encoding="utf-8")
        store.log(me.id, "labels_exported", f"{manifest['rows']} approved images, sha256 {manifest['sha256'][:12]}")
        return ok({"manifest": manifest, "labels_jsonl": text})

    @app.get("/api/v1/admin/events")
    async def events(request: Request):
        admin(request)
        return ok({"items": store.events()})

    return store


def sessions_view(state, revoke, clock) -> SimpleNamespace:
    """Adapter over the in-memory auth state, so admin code never touches tokens."""
    def signed_in() -> set[int]:
        with state.lock:
            return {user_id for user_id, token_hash in state.active_login.items() if state.logins[token_hash].expires() > clock()}

    def revoke_user(user_id: int) -> None:
        with state.lock:
            token_hash = state.active_login.get(user_id)
            if token_hash:
                revoke(token_hash)
    return SimpleNamespace(signed_in=signed_in, revoke=revoke_user)

