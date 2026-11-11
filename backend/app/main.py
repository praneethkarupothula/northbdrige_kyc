"""FastAPI application factory. Run with: uvicorn app.main:app"""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import applications, auth, documents, staff
from app.core.config import get_settings
from app.core.db import get_engine
from app.observability.telemetry import configure_logging, configure_tracing, get_logger, safe_error_class

log = get_logger()


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    configure_tracing(s.otel_service_name, s.otel_exporter_otlp_endpoint)
    log.info("api_starting", env=s.app_env, milestone="config_loaded")
    s.validate_database_url()

    app = FastAPI(
        title="Northbridge KYC API",
        version="0.1.0",
        description=(
            "Applicant onboarding and document/name-consistency checks with mandatory human review. "
            "Results describe document consistency only; they do not authenticate documents against government "
            "records and are not credit decisions."
        ),
        docs_url="/docs" if s.app_env != "production" else None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=s.allowed_origins, allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        rid = uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=rid)
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > s.max_request_bytes:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        t0 = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        route = request.scope.get("route")
        log.info("http_request", request_id=rid, method=request.method,
                 route=getattr(route, "path", "/unmatched"), status_code=response.status_code,
                 duration_ms=int((time.monotonic() - t0) * 1000))
        response.headers.setdefault("X-Request-ID", rid)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/v1/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if s.app_env == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Never echo submitted values (they may be passwords or personal data).
        errors = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg", "invalid")} for e in exc.errors()]
        return JSONResponse({"detail": "Some fields are invalid.", "errors": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        if isinstance(exc, HTTPException):
            raise exc
        log.error("unhandled_exception", error_class=safe_error_class(exc))
        return JSONResponse({"detail": "An internal error occurred."}, status_code=500)

    app.include_router(auth.router)
    app.include_router(applications.router)
    app.include_router(documents.router)
    app.include_router(staff.router)
    app.include_router(staff.admin)
    if s.app_env != "production":
        app.include_router(auth.dev_router)

    @app.get("/health", tags=["ops"])
    def health():
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    def ready():
        from app.cli import migration_status

        checks = {}
        try:
            with get_engine().connect() as c:
                c.execute(text("SELECT 1"))
            checks["database"] = "ok"
            cur, head = migration_status()
            checks["migrations"] = "ok" if cur == head else f"pending (db={cur}, head={head})"
        except Exception as exc:  # noqa: BLE001
            checks["database"] = f"error: {safe_error_class(exc)}"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)

    log.info("api_ready", milestone="routes_registered")
    return app


_app = None


def __getattr__(name):  # lazy: `uvicorn app.main:app` builds the app; importing the module does not
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
