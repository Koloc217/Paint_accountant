"""Sign-in endpoints and the check that guards the API when sign-in is on."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from . import auth
from .api import Conn
from .db import connect
from .schemas import NewPasswordIn, PasswordIn

router = APIRouter(prefix="/api/auth", tags=["sign-in"])

# reachable without signing in
PUBLIC_API = {"/api/health", "/api/auth", "/api/auth/login", "/api/auth/logout"}
DOCS = ("/docs", "/redoc", "/openapi.json")


def _signed_in(request: Request, conn: sqlite3.Connection) -> bool:
    return auth.token_valid(conn, request.cookies.get(auth.COOKIE))


def _set_cookie(response: Response, conn: sqlite3.Connection) -> None:
    response.set_cookie(auth.COOKIE, auth.make_token(conn), max_age=auth.COOKIE_MAX_AGE,
                        httponly=True, samesite="lax", path="/")


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.get("")
def status(request: Request, conn: sqlite3.Connection = Conn) -> dict:
    """Whether sign-in is on and whether this device is signed in."""
    on = auth.enabled(conn)
    return {"enabled": on, "user": auth.USER, "signed_in": (not on) or _signed_in(request, conn)}


@router.post("/login")
def login(body: PasswordIn, request: Request, response: Response, conn: sqlite3.Connection = Conn) -> dict:
    if not auth.enabled(conn):
        return {"enabled": False, "signed_in": True}
    throttle: auth.Throttle = request.app.state.throttle
    who = _client(request)
    wait = throttle.wait_seconds(who)
    if wait:
        raise HTTPException(429, {"code": "too_many_attempts", "wait": wait,
                                  "message": f"too many wrong passwords, wait {wait} s"})
    if not auth.check_password(conn, body.password):
        throttle.failed(who)
        raise HTTPException(401, {"code": "wrong_password", "message": "wrong password"})
    throttle.succeeded(who)
    _set_cookie(response, conn)
    return {"enabled": True, "signed_in": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE, path="/")
    return {"signed_in": False}


@router.post("/password")
def set_password(body: NewPasswordIn, response: Response, conn: sqlite3.Connection = Conn) -> dict:
    """Turn sign-in on, or change the password. Other devices will have to sign in again."""
    auth.set_password(conn, body.password)
    _set_cookie(response, conn)  # this device stays signed in
    return {"enabled": True, "signed_in": True}


@router.post("/disable")
def disable(response: Response, conn: sqlite3.Connection = Conn) -> dict:
    auth.disable(conn)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"enabled": False, "signed_in": True}


def install(app) -> None:
    """Guard the API and docs: with sign-in on, only signed-in devices get through."""
    app.state.throttle = auth.Throttle()

    @app.middleware("http")
    async def require_sign_in(request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        guarded = (path.startswith("/api") and path not in PUBLIC_API) or path.startswith(DOCS)
        if guarded:
            conn = connect(request.app.state.settings.db_path)
            try:
                allowed = not auth.enabled(conn) or _signed_in(request, conn)
            finally:
                conn.close()
            if not allowed:
                if path.startswith("/api"):
                    return JSONResponse({"detail": {"code": "auth_required", "message": "sign in first"}},
                                        status_code=401)
                return RedirectResponse("/#/login")
        return await call_next(request)
