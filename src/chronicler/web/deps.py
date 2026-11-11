"""Shared web plumbing: who is asking, which view they get, and the write guard.

* Every page and API call needs a logged-in user (except login/setup and static files).
* A **Player** always gets the Player view; a **GM** picks GM or Player with a cookie.
* Every request that changes something passes :func:`guard_write`: it must be JSON, carry
  the ``X-Chronicler`` header, and (when the browser says) come from this same site.
  Other websites cannot add that header without our permission, so they cannot make a
  logged-in browser change anything here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from chronicler import home
from chronicler.auth import AuthStore, User
from chronicler.campaign import Campaign, View
from chronicler.changes import ChangeService
from chronicler.schema import TYPE_LABELS

HERE = Path(__file__).parent
VIEW_COOKIE = "chronicler_view"
SESSION_COOKIE = "chronicler_session"
WRITE_HEADER = "X-Chronicler"
TREE_TYPES = ("location", "faction")

templates = Jinja2Templates(directory=HERE / "templates")


class LoginRequired(Exception):  # noqa: N818 - control flow, not an error
    """No valid session: pages redirect to login (or first-run setup), the API says 401."""


def campaign_of(request: Request) -> Campaign:
    campaign: Campaign = request.app.state.campaign
    return campaign


def changes_of(request: Request) -> ChangeService:
    service: ChangeService = request.app.state.changes
    return service


def auth_of(request: Request) -> AuthStore:
    store: AuthStore = request.app.state.auth
    return store


def current_user(request: Request) -> User | None:
    return auth_of(request).session_user(request.cookies.get(SESSION_COOKIE))


@dataclass
class Ctx:
    """Per-request state shared by every page."""

    request: Request
    campaign: Campaign
    view: View
    user: User

    @property
    def gm(self) -> bool:
        """Showing the GM view (only ever true for a GM)."""
        return self.view.gm

    @property
    def can_edit(self) -> bool:
        return self.user.gm and self.view.gm

    def base(self, **extra: Any) -> dict[str, Any]:
        index = self.view.index
        counts = index.type_counts()
        today = home.current_date(self.view, self.campaign.calendar)
        sidebar = []
        for entry_type, label in TYPE_LABELS.items():
            if not counts.get(entry_type):
                continue
            tree = index.tree(entry_type) if entry_type in TREE_TYPES else None
            sidebar.append(
                {"type": entry_type, "label": label, "count": counts[entry_type], "tree": tree}
            )
        return {
            "request": self.request,
            "campaign_name": self.campaign.config.name,
            "gm": self.gm,
            "user": self.user,
            "can_edit": self.can_edit,
            "type_labels": TYPE_LABELS,
            "sidebar": sidebar,
            "today": self.campaign.calendar.format(today) if today else None,
            "query": "",
            **extra,
        }


def get_ctx(request: Request) -> Ctx:
    """Per-request context: reload changed files, check the login, then pick the view."""
    user = current_user(request)
    if user is None:
        raise LoginRequired
    campaign = campaign_of(request)
    campaign.refresh_if_changed()
    gm_view = user.gm and request.cookies.get(VIEW_COOKIE, "gm") != "player"
    return Ctx(request, campaign, campaign.view(gm=gm_view), user)


CtxDep = Annotated[Ctx, Depends(get_ctx)]


def require_gm(ctx: CtxDep) -> Ctx:
    """GM role required (the GM may be looking at either view)."""
    if not ctx.user.gm:
        raise HTTPException(403, "GM only")
    return ctx


GmDep = Annotated[Ctx, Depends(require_gm)]


def guard_write(request: Request) -> None:
    """Refuse requests that could have been forged by another website."""
    if request.headers.get(WRITE_HEADER) != "1":
        raise HTTPException(403, f"missing {WRITE_HEADER} header")
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "send JSON")
    origin = request.headers.get("origin")
    if origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}":
        raise HTTPException(403, "cross-site request refused")
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        raise HTTPException(403, "cross-site request refused")


WriteGuard = Depends(guard_write)


def page(ctx: Ctx, template: str, status_code: int = 200, **extra: Any) -> HTMLResponse:
    return templates.TemplateResponse(
        ctx.request, template, ctx.base(**extra), status_code=status_code
    )
