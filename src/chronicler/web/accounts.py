"""Login, first-run setup, logout, and the GM's Users page (HTMX forms, HTML replies)."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import Markup

from chronicler.auth import SESSION_DAYS, AuthError, User
from chronicler.web.deps import (
    SESSION_COOKIE,
    GmDep,
    WriteGuard,
    auth_of,
    campaign_of,
    current_user,
    page,
    templates,
)

router = APIRouter()
FormBody = Annotated[dict[str, Any], Body()]


def safe_next(target: str | None) -> str:
    """Only ever redirect within this site."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target if urlparse(target).netloc == "" else "/"
    return "/"


def message(text: str, *, error: bool = True, status_code: int = 200) -> HTMLResponse:
    css = "form-error" if error else "form-ok"
    return HTMLResponse(f'<p class="{css}">{Markup.escape(text)}</p>', status_code=status_code)


def go(url: str) -> Response:
    response = Response(status_code=204)
    response.headers["HX-Redirect"] = url
    return response


def _logged_in(request: Request, user: User, next_url: str = "/") -> Response:
    response = go(safe_next(next_url))
    response.set_cookie(
        SESSION_COOKIE,
        auth_of(request).start_session(user),
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


def _bare(request: Request, template: str, **extra: Any) -> HTMLResponse:
    name = campaign_of(request).config.name
    return templates.TemplateResponse(request, template, {"campaign_name": name, **extra})


def _text(form: dict[str, Any], key: str) -> str:
    return str(form.get(key) or "")


# -- pages -------------------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/") -> Response:
    if not auth_of(request).has_users():
        return RedirectResponse("/setup", status_code=303)
    if current_user(request) is not None:
        return RedirectResponse(safe_next(next), status_code=303)
    return _bare(request, "login.html", next=safe_next(next))


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request) -> Response:
    if auth_of(request).has_users():
        return RedirectResponse("/login", status_code=303)
    return _bare(request, "setup.html")


@router.get("/users", response_class=HTMLResponse)
def users_page(ctx: GmDep) -> HTMLResponse:
    return page(ctx, "users.html", users=auth_of(ctx.request).users())


# -- actions -------------------------------------------------------------------------------------


@router.post("/login", dependencies=[WriteGuard])
def login(request: Request, form: FormBody, next: str = "/") -> Response:
    result = auth_of(request).authenticate(_text(form, "username"), _text(form, "password"))
    if result.user is not None:
        return _logged_in(request, result.user, next)
    if result.retry_after:
        return message(f"Too many tries. Wait {result.retry_after} seconds and try again.")
    return message("Wrong name or password.")


@router.post("/setup", dependencies=[WriteGuard])
def setup(request: Request, form: FormBody) -> Response:
    store = auth_of(request)
    if store.has_users():
        raise HTTPException(403, "setup is already done")
    if _text(form, "password") != _text(form, "confirm"):
        return message("The two passwords do not match.")
    try:
        user = store.create_user(_text(form, "username"), _text(form, "password"), "gm")
    except AuthError as exc:
        return message(str(exc))
    return _logged_in(request, user)


@router.post("/logout", dependencies=[WriteGuard])
def logout(request: Request) -> Response:
    auth_of(request).end_session(request.cookies.get(SESSION_COOKIE))
    response = go("/login")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/users", dependencies=[WriteGuard])
def add_user(ctx: GmDep, form: FormBody) -> Response:
    try:
        auth_of(ctx.request).create_user(
            _text(form, "username"), _text(form, "password"), _text(form, "role") or "player"
        )
    except AuthError as exc:
        return message(str(exc))
    return go("/users")


@router.post("/users/{username}/password", dependencies=[WriteGuard])
def reset_password(ctx: GmDep, username: str, form: FormBody) -> Response:
    try:
        auth_of(ctx.request).set_password(username, _text(form, "password"))
    except AuthError as exc:
        return message(str(exc))
    return message(f"Password changed; {username} is logged out everywhere.", error=False)


@router.post("/users/{username}/remove", dependencies=[WriteGuard])
def remove_user(ctx: GmDep, username: str) -> Response:
    try:
        auth_of(ctx.request).remove_user(username, acting=ctx.user)
    except AuthError as exc:
        return message(str(exc))
    return go("/users")
