"""Operator authentication for the human approval gate.

The browser UI must never see APPROVAL_TOKEN (no Vite env, no bundle, no
localStorage). Instead the operator trades the token once for a session:
POST /api/auth/login {"token": "..."} sets an HttpOnly + SameSite=Lax
cookie, and the approve/reject endpoints accept that cookie as equivalent
to the header token. Sessions live in memory (a restart logs everyone
out) and expire after SESSION_TTL_SEC. The token is never logged;
failures are a generic 401 either way so responses cannot oracle it.
"""

import hmac
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "agent_session"
SESSION_TTL_SEC = 12 * 3600

_sessions: dict[str, float] = {}


def _prune() -> None:
    now = time.time()
    for sid, created in list(_sessions.items()):
        if now - created > SESSION_TTL_SEC:
            _sessions.pop(sid, None)


def create_session() -> str:
    _prune()
    sid = secrets.token_hex(32)
    _sessions[sid] = time.time()
    return sid


def valid_session(sid: str | None) -> bool:
    if not sid:
        return False
    created = _sessions.get(sid)
    if created is None:
        return False
    if time.time() - created > SESSION_TTL_SEC:
        _sessions.pop(sid, None)
        return False
    return True


def revoke_session(sid: str | None) -> None:
    if sid:
        _sessions.pop(sid, None)


def _token_configured() -> bool:
    return bool(settings.approval_token)


def credentials_ok(provided: str) -> bool:
    expected = settings.approval_token
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)


def session_authorized(request: Request) -> bool:
    return valid_session(request.cookies.get(COOKIE_NAME))


class LoginBody(BaseModel):
    token: str = ""


@router.get("/status")
async def auth_status(request: Request):
    """Whether this browser currently holds an operator session."""
    return {"authenticated": _token_configured() and session_authorized(request),
            "token_required": _token_configured()}


@router.post("/login")
async def login(body: LoginBody, response: Response):
    if not credentials_ok(body.token):
        # Generic either way: unconfigured server and wrong token look
        # identical, and nothing about the expected value leaks.
        raise HTTPException(status_code=401, detail="operator sign-in failed")
    sid = create_session()
    response.set_cookie(
        COOKIE_NAME, sid, httponly=True, samesite="lax", path="/",
        max_age=SESSION_TTL_SEC,
    )
    return {"authenticated": True}


@router.post("/logout")
async def logout(request: Request, response: Response):
    revoke_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"authenticated": False}
