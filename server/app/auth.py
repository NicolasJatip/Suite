from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer
from passlib.context import CryptContext
from starlette.requests import Request
from starlette.responses import RedirectResponse

from .config import Settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_serializer(settings: Settings) -> URLSafeSerializer:
    return URLSafeSerializer(settings.session_secret, salt="mini-portal-session")


def verify_credentials(settings: Settings, username: str, password: str) -> bool:
    if username != settings.admin_user:
        return False
    stored = settings.admin_password
    if stored.startswith("$2"):
        return pwd_context.verify(password, stored)
    return password == stored


def login_response(settings: Settings, username: str) -> RedirectResponse:
    response = RedirectResponse(url="/home", status_code=303)
    token = get_serializer(settings).dumps({"sub": username})
    response.set_cookie(
        "mini_portal_session",
        token,
        httponly=True,
        secure=settings.public_scheme == "https",
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return response


def logout_response() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("mini_portal_session")
    return response


def user_from_session_token(settings: Settings, token: str | None) -> str | None:
    if not token:
        return None
    try:
        payload = get_serializer(settings).loads(token)
    except (BadSignature, TypeError):
        return None
    username = payload.get("sub")
    if username == settings.admin_user:
        return username
    return None


def current_user(request: Request, settings: Settings) -> str | None:
    return user_from_session_token(settings, request.cookies.get("mini_portal_session"))


def require_user(request: Request, settings: Settings) -> str:
    user = current_user(request, settings)
    if user:
        return user
    raise PermissionError("login required")
