import secrets
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from thorax.bootstrap import Container
from thorax.config import Settings
from thorax.domain.errors import DomainError
from thorax.telemetry import OperationalLog

from .schemas import Approval, AssignmentCreate, BoxCreate, BoxEdit, DoctorCreate, DoctorState, FindingsEdit, FixtureImport, Login, ReportEdit, Schema, SubmitRequest
from .body_limit import BodyLimitMiddleware


class HTTPContext:
    def __init__(self, container):
        self.container = container

    def __call__(self, request: Request):
        settings = self.container.settings
        mutation = request.method not in {"GET", "HEAD", "OPTIONS"}
        if mutation and request.headers.get("origin") != settings.app_origin:
            raise DomainError("CSRF_INVALID", 403)
        csrf = request.headers.get("x-csrf-token") if mutation else None
        if mutation and not csrf:
            raise DomainError("CSRF_INVALID", 403)
        return self.container.auth.authenticate(request.cookies.get(settings.policy["security"]["cookie_name"]), activity=mutation or request.headers.get("x-user-activity") == "1", csrf=csrf)

    @staticmethod
    def revision(request):
        value = request.headers.get("if-match")
        if value is None:
            return None
        if not value.startswith('"') or not value.endswith('"') or not value[1:-1].isdigit():
            raise DomainError("VALIDATION_ERROR", 422)
        return int(value[1:-1])

    def headers(self, request):
        return self.revision(request), request.headers.get("idempotency-key")

    @staticmethod
    def result(response, value):
        revision = value.get("session_revision") or value.get("session", {}).get("revision")
        if revision:
            response.headers["ETag"] = f'"{revision}"'
        return value


class Routes:
    def __init__(self, container):
        self.container = container
        self.context = HTTPContext(container)
        self.router = APIRouter(prefix="/api/v1")
        for method, path, handler, status in (
            ("GET", "/health", self.health, 200),
            ("POST", "/auth/login", self.login, 200),
            ("POST", "/auth/logout", self.logout, 204),
        ):
            self.router.add_api_route(path, handler, methods=[method], status_code=status)
        self._protected()

    def health(self):
        return {"status": "ok"}

    def login(self, payload: Login, request: Request, response: Response):
        settings = self.container.settings
        if request.headers.get("origin") != settings.app_origin or request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise DomainError("CSRF_INVALID", 403)
        if len(payload.email) > settings.policy["limits"]["email_chars"] or len(payload.password.encode()) > settings.policy["security"]["password_max_bytes"]:
            raise DomainError("VALIDATION_ERROR", 422)
        user, token = self.container.auth.login(payload.email, payload.password, request.client.host if request.client else "unknown")
        response.set_cookie(settings.policy["security"]["cookie_name"], token, httponly=True, secure=not settings.insecure_cookie, samesite="lax", path="/")
        return user

    def logout(self, request: Request):
        settings = self.container.settings
        token = request.cookies.get(settings.policy["security"]["cookie_name"])
        if token:
            try:
                self.context(request)
            except DomainError as error:
                if error.status != 401:
                    raise
        self.container.auth.logout(token)
        response = Response(status_code=204)
        response.delete_cookie(settings.policy["security"]["cookie_name"], path="/")
        return response

    def _protected(self):
        actor = Depends(self.context)
        reading, admin, jobs = self.container.reading, self.container.admin, self.container.jobs

        @self.router.get("/auth/me")
        def me(user=actor):
            return user

        @self.router.get("/patients")
        def patients(search: str | None = None, cursor: str | None = None, limit: int | None = None, user=actor):
            return reading.patients(user["id"], search, cursor, limit)

        @self.router.get("/patients/{patient_id}/studies")
        def studies(patient_id: int, cursor: str | None = None, limit: int | None = None, user=actor):
            return reading.studies(user["id"], patient_id, cursor, limit)

        @self.router.get("/studies/{study_id}")
        def study(study_id: int, user=actor):
            return reading.study(user["id"], study_id)

        @self.router.get("/images/{image_id}/content")
        def image(image_id: int, user=actor):
            payload, media_type = reading.image(user["id"], image_id)
            return Response(payload, media_type=media_type)

        @self.router.post("/studies/{study_id}/sessions")
        def open_session(study_id: int, response: Response, payload: Schema | None = None, user=actor):
            result, created = reading.open(user["id"], study_id)
            response.status_code = 201 if created else 200
            return self.context.result(response, result)

        @self.router.get("/sessions/{session_id}")
        def workspace(session_id: int, response: Response, user=actor):
            return self.context.result(response, reading.workspace(user["id"], session_id))

        @self.router.get("/sessions/{session_id}/messages")
        def messages(session_id: int, cursor: str | None = None, limit: int | None = None, user=actor):
            return reading.messages(user["id"], session_id, cursor, limit)

        @self.router.get("/sessions/{session_id}/events")
        def events(session_id: int, cursor: str | None = None, limit: int | None = None, user=actor):
            return reading.events(user["id"], session_id, cursor, limit)

        @self.router.post("/sessions/{session_id}/boxes", status_code=201)
        def create_box(session_id: int, payload: BoxCreate, request: Request, response: Response, user=actor):
            return self.context.result(response, reading.create_box(user["id"], session_id, payload.model_dump(), *self.context.headers(request)))

        @self.router.patch("/sessions/{session_id}/boxes/{box_id}")
        def edit_box(session_id: int, box_id: int, payload: BoxEdit, request: Request, response: Response, user=actor):
            return self.context.result(response, reading.edit_box(user["id"], session_id, box_id, payload.model_dump(exclude_unset=True), *self.context.headers(request)))

        @self.router.delete("/sessions/{session_id}/boxes/{box_id}")
        def delete_box(session_id: int, box_id: int, request: Request, response: Response, user=actor):
            return self.context.result(response, reading.delete_box(user["id"], session_id, box_id, *self.context.headers(request)))

        @self.router.patch("/sessions/{session_id}/findings")
        def findings(session_id: int, payload: FindingsEdit, request: Request, response: Response, user=actor):
            return self.context.result(response, reading.save_findings(user["id"], session_id, payload.model_dump(), *self.context.headers(request)))

        @self.router.post("/sessions/{session_id}/findings/approve")
        def gate1(session_id: int, payload: Approval, request: Request, response: Response, user=actor):
            return self.context.result(response, reading.approve_findings(user["id"], session_id, payload.model_dump(), *self.context.headers(request)))

        @self.router.patch("/sessions/{session_id}/reports/{report_id}")
        def report(session_id: int, report_id: int, payload: ReportEdit, request: Request, response: Response, user=actor):
            return self.context.result(response, reading.edit_report(user["id"], session_id, report_id, payload.model_dump(exclude_unset=True), *self.context.headers(request)))

        @self.router.post("/sessions/{session_id}/reports/{report_id}/approve")
        def gate2(session_id: int, report_id: int, payload: Approval, request: Request, response: Response, user=actor):
            return self.context.result(response, reading.approve_report(user["id"], session_id, report_id, payload.model_dump(), *self.context.headers(request)))

        @self.router.post("/sessions/{session_id}/ai-requests", status_code=202)
        def submit(session_id: int, payload: SubmitRequest, request: Request, response: Response, user=actor):
            job = jobs.submit(user["id"], session_id, payload.model_dump(exclude_none=True),
                              *self.context.headers(request), request_id=request.state.request_id)
            request.state.job_id = job["request_id"]
            return self.context.result(response, job)

        @self.router.get("/sessions/{session_id}/ai-requests/{request_id}")
        def poll(session_id: int, request_id: int, response: Response, user=actor):
            return self.context.result(response, jobs.get(user["id"], session_id, request_id))

        @self.router.get("/admin/doctors")
        def doctors(cursor: str | None = None, limit: int | None = None, user=actor):
            return admin.doctors(user["id"], cursor, limit)

        @self.router.post("/admin/doctors", status_code=201)
        def doctor_create(payload: DoctorCreate, request: Request, user=actor):
            return admin.create_doctor(user["id"], payload.model_dump(), request.headers.get("idempotency-key"))

        @self.router.patch("/admin/doctors/{doctor_id}")
        def doctor_state(doctor_id: int, payload: DoctorState, request: Request, user=actor):
            return admin.doctor_state(user["id"], doctor_id, payload.model_dump(), request.headers.get("idempotency-key"))

        @self.router.get("/admin/patient-catalog")
        def catalogue(search: str | None = None, cursor: str | None = None, limit: int | None = None, user=actor):
            return admin.catalogue(user["id"], search, cursor, limit)

        @self.router.post("/admin/assignments", status_code=201)
        def assign(payload: AssignmentCreate, request: Request, user=actor):
            return admin.assign(user["id"], payload.model_dump(), request.headers.get("idempotency-key"))

        @self.router.delete("/admin/assignments/{doctor_id}/{patient_id}", status_code=204)
        def revoke(doctor_id: int, patient_id: int, user=actor):
            admin.revoke(user["id"], doctor_id, patient_id)
            return Response(status_code=204)

        @self.router.get("/admin/fixtures/imports/{fixture_key}")
        def fixture_status(fixture_key: str, user=actor):
            return admin.fixture_status(user["id"], fixture_key)

        @self.router.post("/admin/fixtures/imports")
        def fixture_import(payload: FixtureImport, request: Request, user=actor):
            return admin.import_fixture(user["id"], payload.fixture_key, request.headers.get("idempotency-key"))


def create_app(container: Container | None = None) -> FastAPI:
    supplied = container is not None
    container = container or Container(Settings.from_environment())

    @asynccontextmanager
    async def lifespan(_app):
        OperationalLog.configure()
        yield
        if not supplied:
            container.close()

    app = FastAPI(title="Thorax API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.container = container
    app.include_router(Routes(container).router)
    app.add_middleware(BodyLimitMiddleware, max_bytes=container.settings.policy["limits"]["payload_bytes"])

    @app.middleware("http")
    async def response_policy(request, call_next):
        request.state.request_id = secrets.token_urlsafe()
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            response = error_response(request, "INTERNAL_ERROR", 500)
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Request-ID"] = request.state.request_id
        route = getattr(request.scope.get("route"), "path", None)
        OperationalLog.write("api", request_id=request.state.request_id,
                             route=f"{request.method} {route}" if route else None,
                             status=response.status_code, job_id=getattr(request.state, "job_id", None),
                             latency_ms=int((time.monotonic() - started) * 1000),
                             error_code=getattr(request.state, "error_code", None))
        return response

    def error_response(request, code, status, details=None):
        request.state.error_code = code
        headers = {"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"}
        if status == 429:
            headers["Retry-After"] = str(container.settings.policy["security"]["login_window_seconds"])
        return JSONResponse({"error": {"code": code, "message": code.replace("_", " ").capitalize(), "request_id": getattr(request.state, "request_id", secrets.token_urlsafe()), "details": details or []}}, status_code=status, headers=headers)

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return error_response(request, error.code, error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return error_response(request, "VALIDATION_ERROR", 422, [{"field": ".".join(map(str, e["loc"])), "reason": e["type"]} for e in error.errors()])

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return error_response(request, "RESOURCE_NOT_FOUND" if error.status_code == 404 else "HTTP_ERROR", error.status_code)

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(Exception)
    async def internal_error(request, _error):
        return error_response(request, "INTERNAL_ERROR", 500)

    return app
