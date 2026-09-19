"""Phase 1 HTTP API. Start with uvicorn smartplanner.api:create_app --factory."""

import re
import secrets
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .accounts import (
    AuthLimiter, Credentials, Passwords, PreferencePut, PreferenceRecord,
    Registration, SessionView, UserView, csrf_for_token,
)
from .database import connect
from .errors import AppError
from .migrate import LATEST_SCHEMA
from .models import TaskCreate, TaskPatch, TaskRecord
from .repository import Repository
from .settings import Settings

COOKIE_NAME = "smartplanner_session"
TOKEN_FORMAT = re.compile(r"^[A-Za-z0-9_-]{43}$")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_environment()
    app = FastAPI(title="SmartPlanner", version="0.2.0", redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(config.origin).hostname])
    passwords = Passwords()
    limiter = AuthLimiter(config.auth_requests_per_minute)

    @app.middleware("http")
    async def origin_and_response_headers(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin is not None and origin != config.origin:
                return JSONResponse(status_code=403, content={"code": "origin_rejected", "message": "مبدأ درخواست معتبر نیست."})
            # Reject oversized declared bodies before decoding/hashing user input.
            length = request.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > 32_768):
                return JSONResponse(status_code=413, content={"code": "request_too_large", "message": "حجم درخواست زیاد است."})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        headers = {"Retry-After": "60"} if exc.status == 429 else None
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message}, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # FastAPI's default validation response includes input values, including bad passwords.
        errors = [{"field": list(error["loc"]), "type": error["type"]} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"code": "invalid_input", "message": "اطلاعات ورودی معتبر نیست.", "errors": errors})

    @app.exception_handler(psycopg.DatabaseError)
    async def database_error_handler(request: Request, exc: psycopg.DatabaseError):
        return JSONResponse(status_code=503, content={"code": "storage_unavailable", "message": "ذخیره‌سازی موقتاً در دسترس نیست؛ دوباره تلاش کنید."})

    def repository():
        with connect(config.database_url) as connection:
            yield Repository(connection)

    Repo = Annotated[Repository, Depends(repository)]

    def session_token(request: Request) -> str:
        token = request.cookies.get(COOKIE_NAME, "")
        if not TOKEN_FORMAT.fullmatch(token):
            raise AppError(401, "authentication_required", "وارد حساب شوید.")
        return token

    def current_user(request: Request, repo: Repo) -> UserView:
        token = session_token(request)
        user = repo.session_user(token)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.headers.get("x-csrf-token", "")
            if not re.fullmatch(r"[0-9a-f]{64}", supplied) or not secrets.compare_digest(supplied, csrf_for_token(token)):
                raise AppError(403, "csrf_rejected", "درخواست تغییر معتبر نیست؛ صفحه را تازه کنید.")
        return user

    User = Annotated[UserView, Depends(current_user)]

    def limit_auth(request: Request):
        # Do not trust an arbitrary X-Forwarded-For header from the public client.
        limiter.check(request.client.host if request.client else "unknown")

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/api/v1/ready")
    def ready(repo: Repo):
        row = repo.conn.execute("SELECT max(version) AS version FROM schema_migrations").fetchone()
        if row["version"] != LATEST_SCHEMA:
            raise AppError(503, "schema_not_ready", "طرح پایگاه داده آماده نیست.")
        return {"status": "ready"}

    @app.post("/api/v1/auth/register", response_model=UserView, status_code=201)
    def register(data: Registration, request: Request, repo: Repo):
        limit_auth(request)
        return repo.register(data, passwords.hash(data.password.get_secret_value()))

    @app.post("/api/v1/auth/login", response_model=SessionView)
    def login(data: Credentials, request: Request, response: Response, repo: Repo):
        limit_auth(request)
        account = repo.account_for_login(data.email)
        if not passwords.verify(data.password.get_secret_value(), account["password_hash"] if account else None):
            raise AppError(401, "invalid_credentials", "ایمیل یا رمز درست نیست.")
        token = repo.create_session(account["id"], config.session_hours)
        response.set_cookie(
            COOKIE_NAME, token, httponly=True, secure=config.cookie_secure,
            samesite="lax", max_age=config.session_hours * 3600, path="/",
        )
        user = UserView.model_validate({key: value for key, value in account.items() if key != "password_hash"})
        return SessionView(user=user, csrf_token=csrf_for_token(token))

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request, response: Response, user: User, repo: Repo):
        repo.logout(session_token(request))
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=config.cookie_secure, samesite="lax")

    @app.get("/api/v1/auth/session", response_model=SessionView)
    def session(request: Request, user: User):
        return SessionView(user=user, csrf_token=csrf_for_token(session_token(request)))

    @app.get("/api/v1/me", response_model=UserView)
    def me(user: User):
        return user

    @app.get("/api/v1/me/preferences", response_model=PreferenceRecord)
    def preferences(user: User, repo: Repo):
        return repo.get_preferences(user.id)

    @app.put("/api/v1/me/preferences", response_model=PreferenceRecord)
    def update_preferences(data: PreferencePut, user: User, repo: Repo):
        return repo.update_preferences(user.id, data)

    @app.post("/api/v1/tasks", response_model=TaskRecord, status_code=201)
    def create_task(data: TaskCreate, response: Response, user: User, repo: Repo):
        task, replayed = repo.create_task(user.id, data)
        if replayed:
            response.status_code = 200
        return task

    @app.get("/api/v1/tasks", response_model=list[TaskRecord])
    def tasks(user: User, repo: Repo, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)):
        return repo.list_tasks(user.id, limit, offset)

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskRecord)
    def get_task(task_id: UUID, user: User, repo: Repo):
        return repo.get_task(user.id, task_id)

    @app.patch("/api/v1/tasks/{task_id}", response_model=TaskRecord)
    def update_task(task_id: UUID, data: TaskPatch, user: User, repo: Repo):
        return repo.update_task(user.id, task_id, data)

    @app.delete("/api/v1/tasks/{task_id}", status_code=204)
    def delete_task(task_id: UUID, user: User, repo: Repo, expected_version: int = Query(..., ge=1, le=2147483647)):
        repo.delete_task(user.id, task_id, expected_version)

    return app
