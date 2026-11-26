"""FastAPI backend for Thorax AI. First slice: authentication.

Implements the contract the web app already speaks (src/thorax-web/app/api/v1/[...path]/route.ts):
an opaque 256-bit session token in an HttpOnly cookie, a CSRF token derived from it for
every non-GET request, an Origin check, generic login errors and login rate limits.
From SYSTEM_DESIGN_PLAN.md it adds: SQLite accounts, token hashes stored in SQLite (sessions.py)
so a restart keeps users signed in, a 2-hour absolute login lifetime, one active login per
account, and an account re-check against the database on every request.

Run:  python scripts/serve_api.py
(One worker: sign-in rate-limit counters live in this process's memory.)
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .accounts import ROLES, AccountStore, User, password_matches, valid_password
from .admin import register_admin, sessions_view
from .catalog import MAX_UPLOAD_BYTES, Catalog, normalize_upload
from .case_search import CaseIndex, register_case_search
from .reading import ReadingStore, register_reading
from .sessions import Login, SessionStore
from .vlm import VlmClient
from .db import utc_now
from .labeling import LabelError, LabelStore, labeling_enabled
from .storage import LocalImageStore

log = logging.getLogger("thorax_api")
COOKIE = "thorax_session"
GENERIC_LOGIN_ERROR = "Email or password is incorrect."
UNAVAILABLE = "This resource is unavailable."
CSRF_MESSAGE = "Refresh the page before trying this action again."


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.retry_after = status, code, message, retry_after


def invalid(message: str) -> ApiError:
    return ApiError(422, "VALIDATION_ERROR", message)


@dataclass
class AuthState:
    secret: bytes
    attempts: dict[str, list[float]] = field(default_factory=dict)  # rate-limit counters; reset on restart
    lock: threading.Lock = field(default_factory=threading.Lock)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def create_app(
    db_path: str | None = None,
    web_origins: list[str] | None = None,
    *,
    clock: Callable[[], float] = time.time,
    idle_seconds: int = 30 * 60,
    absolute_seconds: int = 2 * 60 * 60,
    image_root: str | None = None,
) -> FastAPI:
    db_path = db_path or os.environ.get("THORAX_DATABASE_URL") or os.environ.get("THORAX_DB_PATH", "thorax.sqlite3")
    store = AccountStore(db_path)
    catalog = Catalog(db_path, LocalImageStore(image_root or os.environ.get("THORAX_IMAGE_ROOT", "thorax-images")))
    labels = LabelStore(db_path)
    per_address = int(os.environ.get("THORAX_SIGNIN_PER_ADDRESS_PER_MINUTE", "5"))
    # Set only when the API port is reachable solely through the site's own proxy (docker/compose.yaml).
    trust_proxy = os.environ.get("THORAX_TRUST_PROXY") == "1"
    labeling_on = labeling_enabled()
    origins = web_origins if web_origins is not None else [o.strip().rstrip("/") for o in os.environ.get("THORAX_WEB_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000").split(",") if o.strip()]
    sessions = SessionStore(db_path)
    state = AuthState(sessions.secret())
    app = FastAPI(title="Thorax AI API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.auth = state
    app.state.accounts = store
    app.state.catalog = catalog
    app.state.labels = labels

    def csrf(token: str) -> str:
        return hmac.new(state.secret, f"csrf:{token}".encode(), hashlib.sha256).hexdigest()

    def payload(user: User, token: str, login: Login) -> dict:
        expires = datetime.fromtimestamp(login.expires(), timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return {**user.public(), "csrf_token": csrf(token), "expires_at": expires, "labeling_enabled": labeling_on}

    def check_origin(request: Request) -> str:
        origin = (request.headers.get("origin") or "").rstrip("/")
        if origin not in origins:
            raise ApiError(403, "CSRF_INVALID", CSRF_MESSAGE)
        return origin

    def cookie_header(origin: str, value: str = "", clear: bool = False) -> str:
        parts = urlsplit(origin)
        secure = parts.scheme == "https"
        if not secure and not _is_loopback(parts.hostname) and not clear:
            raise ApiError(403, "ROLE_FORBIDDEN", "Use HTTPS or a loopback address to sign in.")
        return f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Lax{'; Secure' if secure else ''}{'; Max-Age=0' if clear else ''}"

    def client_address(request: Request) -> str:
        peer = request.client.host if request.client else ""
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        # Trust X-Forwarded-For only from a proxy on this machine (the Next.js server), or from any peer with THORAX_TRUST_PROXY=1.
        return forwarded if forwarded and (trust_proxy or _is_loopback(peer)) else peer or "unknown"

    def authenticate(request: Request) -> tuple[User, Login, str, str] | None:
        token = request.cookies.get(COOKIE, "")
        if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            return None
        token_hash = _token_hash(token)
        login = sessions.get(token_hash)
        if login is None:
            return None
        if login.expires() <= clock():
            sessions.revoke(token_hash)
            return None
        user = store.by_id(login.user_id)  # re-read so a disabled account loses access immediately
        if user is None or not user.is_active:
            sessions.revoke(token_hash)
            return None
        return user, login, token, token_hash

    async def read_body(request: Request) -> dict:
        raw = await request.body()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise invalid("Send a valid JSON request.") from None
        if not isinstance(data, dict):
            raise invalid("Send a valid JSON request.")
        return data

    def no_store(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        return response

    def request_id(request: Request) -> str:
        if not hasattr(request.state, "request_id"):
            request.state.request_id = secrets.token_hex(8)
        return request.state.request_id

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        """One line per request with the route template, never the raw path, query, body or user."""
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        log.info("%s %s %s %.0fms id=%s", request.method, route, response.status_code, (time.perf_counter() - started) * 1000, request_id(request))
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError):
        headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
        body = {"error": {"code": error.code, "message": error.message, "request_id": request_id(request), "details": []}}
        return no_store(JSONResponse(body, status_code=error.status, headers=headers))

    @app.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception):
        # Type and location only: the traceback's local values could hold request content.
        frame = error.__traceback__
        while frame and frame.tb_next:
            frame = frame.tb_next
        where = f"{frame.tb_frame.f_code.co_filename.rsplit(os.sep, 1)[-1]}:{frame.tb_lineno}" if frame else "unknown"
        log.error("unhandled %s at %s id=%s", type(error).__name__, where, request_id(request))
        body = {"error": {"code": "INTERNAL_ERROR", "message": "The request could not be completed. Try again.", "request_id": request_id(request), "details": []}}
        return no_store(JSONResponse(body, status_code=500, headers={"X-Request-ID": request_id(request)}))

    @app.get("/api/v1/health")
    async def health():
        return no_store(JSONResponse({"status": "ok"}))

    @app.post("/api/v1/auth/login")
    async def login(request: Request):
        origin = check_origin(request)
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise invalid("Send a valid JSON request.")
        body = await read_body(request)
        if set(body) - {"email", "password"}:
            raise invalid("Unexpected request field.")
        email, password = body.get("email"), body.get("password")
        if not isinstance(email, str) or not email.strip() or len(email) > 254:
            raise invalid("Email must be 1–254 characters.")
        if not isinstance(password, str) or not password or len(password) > 72:
            raise invalid("Password must be 1–72 characters.")
        email = email.strip().lower()
        now = clock()
        account_key = hmac.new(state.secret, f"login:{email}".encode(), hashlib.sha256).hexdigest()
        address_key = hmac.new(state.secret, f"login-address:{client_address(request)}".encode(), hashlib.sha256).hexdigest()
        # Per account: 10 failures / 15 min. Per address: 5 attempts / min (THORAX_SIGNIN_PER_ADDRESS_PER_MINUTE),
        # successful or not; raise it when many users share one public address, such as a clinic network.
        scopes = ((account_key, 15 * 60, 10, True), (address_key, 60, per_address, False))
        with state.lock:
            for key, window, limit, _ in scopes:
                recent = [at for at in state.attempts.get(key, []) if now - at < window]
                state.attempts[key] = recent
                if len(recent) >= limit:
                    raise ApiError(429, "RATE_LIMITED", "Too many sign-in attempts. Wait before trying again.", max(1, int(recent[0] + window - now + 0.999)))
        user = store.by_email(email)
        # Unknown emails are checked against a dummy hash, so timing does not reveal which accounts exist.
        # scrypt takes ~0.3 s and 128 MiB; run it off the event loop.
        matched = valid_password(password, 8) and await run_in_threadpool(password_matches, password, user.password_hash if user else None)
        ok = bool(matched and user and user.is_active)
        if user is not None:
            store.record_sign_in(user.id, ok)  # shown to admins: last sign-in and recent failed attempts
        with state.lock:
            for key, _window, _limit, failures_only in scopes:
                if not failures_only or not ok:
                    state.attempts[key].append(now)
            if not ok or user is None:
                raise ApiError(401, "UNAUTHENTICATED", GENERIC_LOGIN_ERROR)
        token = secrets.token_hex(32)
        session = Login(user.id, now + idle_seconds, now + absolute_seconds)
        sessions.start(_token_hash(token), session, now)  # replaces this account's previous sign-in
        response = JSONResponse(payload(user, token, session))
        response.headers["Set-Cookie"] = cookie_header(origin, token)
        return no_store(response)

    @app.get("/api/v1/auth/me")
    async def me(request: Request):
        auth = authenticate(request)
        if auth is None:
            raise ApiError(401, "UNAUTHENTICATED", UNAVAILABLE)
        user, session, token, token_hash = auth
        if request.headers.get("x-user-activity") == "1":  # polling and background checks do not extend the login
            session.idle_expires = clock() + idle_seconds
            sessions.touch(token_hash, session.idle_expires)
        return no_store(JSONResponse(payload(user, token, session)))

    @app.post("/api/v1/auth/logout")
    async def logout(request: Request):
        origin = check_origin(request)
        auth = authenticate(request)
        if auth is None:
            response = Response(status_code=204)
            response.headers["Set-Cookie"] = cookie_header(origin, clear=True)
            return no_store(response)
        _user, _session, token, token_hash = auth
        if not hmac.compare_digest(csrf(token), request.headers.get("x-csrf-token", "")):
            raise ApiError(403, "CSRF_INVALID", CSRF_MESSAGE)
        if await read_body(request):
            raise invalid("Unexpected request field.")
        sessions.revoke(token_hash)
        response = Response(status_code=204)
        response.headers["Set-Cookie"] = cookie_header(origin, clear=True)
        return no_store(response)

    def actor(request: Request, *roles: str) -> User:
        if request.method != "GET":
            check_origin(request)
        auth = authenticate(request)
        if auth is None:
            raise ApiError(401, "UNAUTHENTICATED", UNAVAILABLE)
        if request.method != "GET" and not hmac.compare_digest(csrf(auth[2]), request.headers.get("x-csrf-token", "")):
            raise ApiError(403, "CSRF_INVALID", CSRF_MESSAGE)
        if auth[0].role not in roles:
            raise ApiError(403, "ROLE_FORBIDDEN", UNAVAILABLE)
        return auth[0]

    @app.post("/api/v1/auth/password")
    async def change_password(request: Request):
        """Any signed-in user changes their own password. The current session stays signed in."""
        user = actor(request, *ROLES)
        body = await read_body(request)
        if set(body) - {"current_password", "new_password"}:
            raise invalid("Unexpected request field.")
        current, new = body.get("current_password"), body.get("new_password")
        if not isinstance(current, str) or not isinstance(new, str):
            raise invalid("Enter your current and new password.")
        key, now = f"password:{user.id}", clock()
        with state.lock:
            recent = [at for at in state.attempts.get(key, []) if now - at < 15 * 60]
            state.attempts[key] = recent
            if len(recent) >= 5:
                raise ApiError(429, "RATE_LIMITED", "Too many attempts. Wait before trying again.", max(1, int(recent[0] + 15 * 60 - now + 0.999)))
        if not current or len(current) > 72 or not await run_in_threadpool(password_matches, current, user.password_hash):
            with state.lock:
                state.attempts[key].append(now)
            raise ApiError(403, "PASSWORD_INCORRECT", "Your current password is incorrect.")
        if not valid_password(new):
            raise invalid("Use a new password of 12–72 UTF-8 bytes.")
        if new == current:
            raise invalid("Choose a password different from your current one.")
        await run_in_threadpool(store.set_password, user.email, new)
        return no_store(Response(status_code=204))

    def page(request: Request, user: User, items: list) -> JSONResponse:
        """Offset pages with HMAC-signed cursors bound to the user, path and search, like the web mock."""
        raw_limit = request.query_params.get("limit")
        limit = 25 if raw_limit is None else (int(raw_limit) if raw_limit.isdigit() and int(raw_limit) > 0 else 0)
        if not 1 <= limit <= 100:
            raise invalid("Invalid page limit." if limit < 1 else "The page size is too large.")
        scope = f"{user.id}:{request.url.path}:{request.query_params.get('search', '')}"
        signed = lambda index: f"{index}." + hmac.new(state.secret, f"cursor:{scope}:{index}".encode(), hashlib.sha256).hexdigest()  # noqa: E731
        start = 0
        if cursor := request.query_params.get("cursor"):
            head = cursor.split(".")[0]
            if not head.isdigit() or not hmac.compare_digest(cursor, signed(int(head))):
                raise invalid("Invalid page cursor.")
            start = int(head)
        end = start + limit
        return no_store(JSONResponse({"items": items[start:end], "next_cursor": signed(end) if end < len(items) else None}))

    def path_id(value: str) -> int:
        if not value.isdigit() or value.startswith("0") or len(value) > 15:
            raise ApiError(404, "RESOURCE_NOT_FOUND", UNAVAILABLE)
        return int(value)

    def missing() -> ApiError:
        return ApiError(404, "RESOURCE_NOT_FOUND", UNAVAILABLE)

    @app.get("/api/v1/patients")
    async def patients(request: Request):
        user = actor(request, "doctor", "annotator")
        search = request.query_params.get("search")
        if search is not None and not 1 <= len(search.strip()) <= 100:
            raise invalid("Search must be 1–100 characters.")
        return page(request, user, catalog.patients_for(user.id, (search or "").strip()))

    @app.get("/api/v1/patients/{patient_id}/studies")
    async def patient_studies(request: Request, patient_id: str):
        user = actor(request, "doctor", "annotator", "admin", "technician")  # admins and technicians see studies to manage intake
        pid = path_id(patient_id)
        may_upload(user, pid)  # a doctor sees only assigned patients; unassigned and nonexistent look the same
        return page(request, user, catalog.studies_for_patient(pid, user.id))

    @app.get("/api/v1/studies/{study_id}")
    async def study(request: Request, study_id: str):
        user = actor(request, "doctor", "annotator", "admin", "technician")
        found = catalog.study(path_id(study_id), user.id)
        if found is None:
            raise missing()
        may_upload(user, found["patient_id"])
        return no_store(JSONResponse(found))

    @app.get("/api/v1/images/{image_id}/content")
    async def image_content(request: Request, image_id: str):
        user = actor(request, "doctor", "annotator")
        image = catalog.image(path_id(image_id))
        allowed = image is not None and (catalog.is_assigned(user.id, image["patient_id"])
                                         or (user.role == "annotator" and labeling_on and labels.annotator_can_view(image["id"], user.id)))
        if not allowed:
            raise missing()
        try:
            data = await run_in_threadpool(catalog.image_bytes, image)
        except (OSError, ValueError):
            raise ApiError(503, "ASSET_UNAVAILABLE", "The study image is unavailable. Try again.") from None
        return no_store(Response(data, media_type=image["media_type"]))

    app.state.admin = register_admin(
        app, accounts=store, catalog=catalog, labels=labels, labeling_on=labeling_on, started_at=utc_now(), actor=actor, page=page, read_body=read_body,
        no_store=no_store, invalid=invalid, error=ApiError, path_id=path_id, sessions=sessions_view(sessions, clock))

    # --- intake: technicians and admins push studies for any patient; doctors read, they do not upload ---
    def may_upload(user: User, patient_id: int) -> None:
        if not catalog.patient_exists(patient_id) or (user.role in ("doctor", "annotator") and not catalog.is_assigned(user.id, patient_id)):
            raise missing()  # unassigned and nonexistent look the same

    def study_fields(study_date, indication) -> tuple[str, str]:
        if not isinstance(study_date, str) or not isinstance(indication, str):
            raise invalid("Enter the study date and indication.")
        try:
            day = datetime.strptime(study_date, "%Y-%m-%d").date()
        except ValueError:
            raise invalid("Enter the study date as YYYY-MM-DD.") from None
        if not datetime(1900, 1, 1).date() <= day <= datetime.fromtimestamp(clock(), timezone.utc).date():
            raise invalid("The study date cannot be in the future.")
        if not 1 <= len(indication.strip()) <= 200:
            raise invalid("Enter the indication (1–200 characters).")
        return study_date, indication.strip()

    def managed_study(request: Request, study_id: str) -> tuple[User, dict]:
        """A technician or admin; doctors read studies but no longer upload or edit them."""
        user = actor(request, "admin", "technician")
        found = catalog.study(path_id(study_id))
        if found is None:
            raise missing()
        may_upload(user, found["patient_id"])
        return user, found

    locked = ApiError(409, "STUDY_LOCKED", "This study can no longer be changed: a reading or labeling has started, or it is part of the demo set.")

    async def upload(request: Request) -> tuple[str, tuple[bytes, int, int]]:
        """Checks the attestation and view, then reads and normalizes the raw image body (no multipart)."""
        query = request.query_params
        if query.get("permitted") != "yes":
            raise invalid("Confirm the image is synthetic or permitted for this demo. Restricted datasets such as MIMIC are not allowed.")
        view = query.get("view", "")
        if view not in ("PA", "AP", "LAT"):
            raise invalid("Choose the view: PA, AP or LAT.")
        if request.headers.get("content-type", "").split(";")[0].strip() not in ("image/png", "image/jpeg"):
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Upload a PNG or JPEG image.")
        too_large = ApiError(413, "UPLOAD_TOO_LARGE", "Upload an image of 10 MiB or less.")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
            raise too_large
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_UPLOAD_BYTES:
                raise too_large
        try:
            return view, await run_in_threadpool(normalize_upload, bytes(data))
        except ValueError as problem:
            raise invalid(str(problem)) from None

    @app.post("/api/v1/patients/{patient_id}/studies")
    async def create_study(request: Request, patient_id: str):
        user = actor(request, "admin", "technician")
        pid = path_id(patient_id)
        may_upload(user, pid)
        study_date, indication = study_fields(request.query_params.get("study_date"), request.query_params.get("indication"))
        priority = request.query_params.get("priority", "routine")
        if priority not in ("routine", "urgent"):
            raise invalid("Choose the priority: routine or urgent.")
        view, image = await upload(request)
        sid = await run_in_threadpool(catalog.create_study, pid, study_date, indication, view, image, user.id, priority)
        app.state.admin.log(user.id, "study_uploaded", f"{dict(catalog.patient_codes())[pid]} study {sid} ({view}), confirmed permitted")
        return no_store(JSONResponse(catalog.study(sid, user.id), status_code=201))

    @app.post("/api/v1/studies/{study_id}/images")
    async def add_study_image(request: Request, study_id: str):
        user, found = managed_study(request, study_id)
        view, image = await upload(request)
        image_id = await run_in_threadpool(catalog.add_image, found["id"], view, image, user.id)
        if image_id is None:
            raise ApiError(409, "STUDY_IN_READING", "This study already has a reading, so its images are fixed. Add a new study instead.")
        app.state.admin.log(user.id, "image_uploaded", f"study {found['id']} image {image_id} ({view}), confirmed permitted")
        return no_store(JSONResponse(catalog.study(found["id"], user.id), status_code=201))

    @app.patch("/api/v1/studies/{study_id}")
    async def edit_study(request: Request, study_id: str):
        user, found = managed_study(request, study_id)
        body = await read_body(request)
        if set(body) - {"study_date", "indication"}:
            raise invalid("Unexpected request field.")
        study_date, indication = study_fields(body.get("study_date"), body.get("indication"))
        if not catalog.update_study(found["id"], study_date, indication):
            raise locked
        app.state.admin.log(user.id, "study_edited", f"study {found['id']}")
        return no_store(JSONResponse(catalog.study(found["id"], user.id)))

    @app.delete("/api/v1/studies/{study_id}")
    async def delete_study(request: Request, study_id: str):
        user, found = managed_study(request, study_id)
        if not await run_in_threadpool(catalog.delete_study, found["id"]):
            raise locked
        app.state.admin.log(user.id, "study_deleted", f"{dict(catalog.patient_codes())[found['patient_id']]} study {found['id']} ({found['image_count']} images)")
        return no_store(Response(status_code=204))

    @app.delete("/api/v1/studies/{study_id}/images/{image_id}")
    async def delete_study_image(request: Request, study_id: str, image_id: str):
        user, found = managed_study(request, study_id)
        outcome = await run_in_threadpool(catalog.delete_image, found["id"], path_id(image_id))
        if outcome == "missing":
            raise missing()
        if outcome == "locked":
            raise locked
        if outcome == "last":
            raise ApiError(409, "LAST_IMAGE", "A study needs at least one image. Delete the study instead.")
        app.state.admin.log(user.id, "image_deleted", f"study {found['id']} image {image_id}")
        return no_store(JSONResponse(catalog.study(found["id"], user.id)))

    def doctor_still_allowed(doctor_id: int, patient_id: int) -> bool:
        user = store.by_id(doctor_id)
        return bool(user and user.is_active and catalog.is_assigned(doctor_id, patient_id))

    readings = ReadingStore(db_path, clock=clock, secret=state.secret, is_allowed=doctor_still_allowed,
                            ai_paused=app.state.admin.ai_paused, record_ai=app.state.admin.record_ai_request,
                            vlm=VlmClient.from_env(), image_png=lambda image_id: catalog.image_bytes(catalog.image(image_id)))
    app.state.readings = readings
    register_reading(app, store=readings, catalog=catalog, actor=actor, path_id=path_id, read_body=read_body, no_store=no_store, error=ApiError)
    app.state.case_index = CaseIndex(db_path)
    register_case_search(app, index=app.state.case_index, catalog=catalog, actor=actor, path_id=path_id, no_store=no_store,
                         invalid=invalid, missing=missing, error=ApiError)

    # --- labeling (annotator role) ---------------------------------------------------------
    @app.exception_handler(LabelError)
    async def label_error(request: Request, error: LabelError):
        return await api_error(request, ApiError(error.status, error.code, error.message))

    def labeler(request: Request) -> User:
        """The labeling queue exists only with THORAX_LABELING; annotators still read their assigned studies without it."""
        user = actor(request, "annotator")
        if not labeling_on:
            raise missing()
        return user

    async def labeling_body(request: Request, allowed: set[str]) -> dict:
        body = await read_body(request)
        if set(body) - allowed:
            raise invalid("Unexpected request field.")
        return body

    @app.get("/api/v1/labeling/vocabulary")
    async def labeling_vocabulary(request: Request):
        labeler(request)
        return no_store(JSONResponse(labels.vocabulary))

    @app.get("/api/v1/labeling/queue")
    async def labeling_queue(request: Request):
        return no_store(JSONResponse(labels.queue(labeler(request).id)))

    @app.post("/api/v1/labeling/tasks/next")
    async def labeling_next(request: Request):
        user = labeler(request)
        await labeling_body(request, set())
        return no_store(JSONResponse(labels.claim_next(user.id)))

    @app.get("/api/v1/labeling/tasks/{task_id}")
    async def labeling_task(request: Request, task_id: str):
        return no_store(JSONResponse(labels.task(path_id(task_id), labeler(request).id)))

    @app.put("/api/v1/labeling/tasks/{task_id}/boxes")
    async def labeling_boxes(request: Request, task_id: str):
        user = labeler(request)
        body = await labeling_body(request, {"boxes", "revision"})
        return no_store(JSONResponse(labels.save_boxes(path_id(task_id), user.id, body.get("boxes"), body.get("revision"))))

    @app.post("/api/v1/labeling/tasks/{task_id}/submit")
    async def labeling_submit(request: Request, task_id: str):
        user = labeler(request)
        body = await labeling_body(request, {"revision"})
        return no_store(JSONResponse(labels.submit(path_id(task_id), user.id, body.get("revision"))))

    @app.post("/api/v1/labeling/tasks/{task_id}/review")
    async def labeling_review(request: Request, task_id: str):
        user = labeler(request)
        body = await labeling_body(request, {"decision", "comment", "revision"})
        return no_store(JSONResponse(labels.review(path_id(task_id), user.id, body.get("decision"), body.get("comment"), body.get("revision"))))

    @app.api_route("/api/v1/{rest:path}", methods=["GET", "POST", "PATCH", "DELETE"])
    async def not_yet(request: Request, rest: str):
        if request.method != "GET":
            check_origin(request)
        auth = authenticate(request)
        if auth is None:
            raise ApiError(401, "UNAUTHENTICATED", UNAVAILABLE)
        if request.method != "GET" and not hmac.compare_digest(csrf(auth[2]), request.headers.get("x-csrf-token", "")):
            raise ApiError(403, "CSRF_INVALID", CSRF_MESSAGE)
        # ponytail: only auth exists so far; clinical/admin endpoints return 501 until they are implemented.
        raise ApiError(501, "NOT_IMPLEMENTED", "This part of the API is not available yet.")

    return app
