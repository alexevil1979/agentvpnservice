from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings


def _ser() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().session_secret, salt="agentvpn-admin")


def create_session_token(username: str) -> str:
    return _ser().dumps({"u": username})


def read_session_token(token: str) -> Optional[str]:
    try:
        data = _ser().loads(token)
        return data.get("u")
    except BadSignature:
        return None


def verify_login(username: str, password: str) -> bool:
    settings = get_settings()
    u_ok = secrets.compare_digest(username, settings.admin_username)
    p_ok = secrets.compare_digest(password, settings.admin_password)
    return u_ok and p_ok


async def require_admin(request: Request) -> str:
    token = request.cookies.get("avs_session")
    if not token:
        raise HTTPException(status_code=401, detail="login required")
    user = read_session_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return user


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)
