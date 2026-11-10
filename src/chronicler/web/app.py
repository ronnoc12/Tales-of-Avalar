"""Read-only web viewer (FastAPI + Jinja + HTMX).

The view mode (GM or Player) comes from a cookie. Player requests only ever touch the
Player view's redacted documents and index, so secrets cannot reach a Player page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from chronicler import home
from chronicler.campaign import Campaign, View
from chronicler.document import Document
from chronicler.redact import find_mismatches
from chronicler.render import HIDDEN_DETAIL_FIELDS, label_for
from chronicler.schema import IMAGE_FIELDS, QUICK_CARD, TYPE_LABELS, is_secret_entry
from chronicler.validate import validate

HERE = Path(__file__).parent
VIEW_COOKIE = "chronicler_view"
TREE_TYPES = ("location", "faction")


@dataclass
class Ctx:
    """Per-request state shared by every page."""

    request: Request
    campaign: Campaign
    view: View

    @property
    def gm(self) -> bool:
        return self.view.gm

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
            "sidebar": sidebar,
            "today": self.campaign.calendar.format(today) if today else None,
            "query": "",
            **extra,
        }


@dataclass
class Row:
    key: str
    label: str
    html: Markup
    party_html: Markup | None = None
    note: str | None = None


@dataclass
class Panel:
    title: str
    items: list[tuple[str, str, str]] = field(default_factory=list)  # (href, label, detail)


def get_ctx(request: Request) -> Ctx:
    """Per-request context: reload changed files, then pick the view from the cookie."""
    campaign: Campaign = request.app.state.campaign
    campaign.refresh_if_changed()
    gm = request.cookies.get(VIEW_COOKIE, "gm") != "player"
    return Ctx(request, campaign, campaign.view(gm=gm))


CtxDep = Annotated[Ctx, Depends(get_ctx)]


def create_app(campaign_root: Path | str) -> FastAPI:
    campaign = Campaign(campaign_root)
    app = FastAPI(title="Chronicler", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    app.state.campaign = campaign

    def page(ctx: Ctx, template: str, status_code: int = 200, **extra: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            ctx.request, template, ctx.base(**extra), status_code=status_code
        )

    def not_found(ctx: Ctx, what: str = "That page") -> HTMLResponse:
        return page(ctx, "not_found.html", status_code=404, what=what)

    @app.exception_handler(404)
    async def _404(request: Request, exc: HTTPException) -> Response:
        return not_found(get_ctx(request))

    # -- mode switch ----------------------------------------------------------------------

    @app.get("/view/{mode}")
    def set_view(mode: str, next: str = "/") -> RedirectResponse:
        target = next if urlparse(next).netloc == "" and next.startswith("/") else "/"
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(VIEW_COOKIE, "player" if mode == "player" else "gm", samesite="lax")
        return response

    # -- home -----------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home_page(ctx: CtxDep) -> HTMLResponse:
        view = ctx.view
        calendar = campaign.calendar
        session = home.last_session(view)
        summary = None
        if session is not None:
            section = session.section("summary")
            summary = Markup(
                view.renderer.section_content(section, session.id)
                if section
                else view.renderer.body(session)
            )
        data: dict[str, Any] = {"last_session": session, "summary": summary}
        if ctx.gm:
            today = home.current_date(view, calendar)
            soon, overdue = home.upcoming(view, calendar, today) if today else ([], [])
            data.update(
                soon=soon,
                overdue=overdue,
                threads=home.hanging_threads(view),
                prep_notes=home.prep_notes(view),
                prep_tasks=home.prep_tasks(view),
                spotlight=home.spotlight(view),
                has_today=today is not None,
            )
        return page(ctx, "home.html", **data)

    # -- entries --------------------------------------------------------------------------

    def rows_for(ctx: Ctx, document: Document, keys: list[str]) -> list[Row]:
        renderer = ctx.view.renderer
        party_view = document.meta.get("party_view") if ctx.gm else None
        overrides = party_view if isinstance(party_view, dict) else {}
        rows: list[Row] = []
        for key in keys:
            value = document.meta.get(key)
            if value in (None, "", [], {}) and key not in overrides:
                continue
            party_html = None
            if key in overrides and overrides[key] != value:
                party_html = Markup(renderer.value(overrides[key], document.id, key))
            note = None
            if ctx.gm and key == "disposition":
                trend = home.score_trend(ctx.view, document.id)
                note = trend.label if trend else None
            rows.append(
                Row(
                    key,
                    label_for(key),
                    Markup(renderer.value(value, document.id, key)),
                    party_html,
                    note,
                )
            )
        return rows

    def panels_for(ctx: Ctx, document: Document) -> list[Panel]:
        index = ctx.view.index
        entry_id = document.id

        def link(other: str) -> str:
            return f"/entry/{other}"

        panels: list[Panel] = []

        def detail(item: dict[str, Any], *keys: str) -> str:
            parts = [str(item[k]) for k in keys if item.get(k) not in (None, "")]
            return " · ".join(parts)

        if index.places_of.get(entry_id):
            panels.append(
                Panel(
                    "Places",
                    [
                        (f"/place/{p.owner_id}/{p.slug}", p.name, p.kind or "")
                        for p in index.places_of[entry_id]
                    ],
                )
            )
        if index.children.get(entry_id):
            panels.append(
                Panel(
                    "Contains",
                    [
                        (link(c), index.name_of(c), TYPE_LABELS.get(index.docs[c].type, ""))
                        for c in sorted(index.children[entry_id], key=index.name_of)
                    ],
                )
            )
        if index.here.get(entry_id):
            panels.append(
                Panel(
                    "Here now",
                    [
                        (link(c), index.name_of(c), TYPE_LABELS.get(index.docs[c].type, ""))
                        for c in sorted(index.here[entry_id], key=index.name_of)
                    ],
                )
            )
        if index.members.get(entry_id):
            panels.append(
                Panel(
                    "Members",
                    [
                        (
                            link(m.source_id),
                            index.name_of(m.source_id),
                            detail(m.details, "rank", "standing"),
                        )
                        for m in index.members[entry_id]
                    ],
                )
            )
        if index.presence.get(entry_id):
            panels.append(
                Panel(
                    "Presence",
                    [
                        (
                            link(m.source_id),
                            index.name_of(m.source_id),
                            detail(m.details, "presence", "role"),
                        )
                        for m in index.presence[entry_id]
                    ],
                )
            )
        if index.cast.get(entry_id):
            panels.append(
                Panel(
                    "Cast",
                    [
                        (
                            link(m.source_id),
                            index.name_of(m.source_id),
                            detail(m.details, "role", "status"),
                        )
                        for m in index.cast[entry_id]
                    ],
                )
            )
        if index.event_cast.get(entry_id):
            panels.append(
                Panel(
                    "Involved",
                    [
                        (
                            link(m.source_id),
                            index.name_of(m.source_id),
                            detail(m.details, "role", "opinion"),
                        )
                        for m in index.event_cast[entry_id]
                    ],
                )
            )
        if index.decisions_for.get(entry_id):
            panels.append(
                Panel(
                    "Decisions",
                    [
                        (link(d), index.name_of(d), str(index.docs[d].meta.get("state", "")))
                        for d in index.decisions_for[entry_id]
                    ],
                )
            )
        if index.incoming.get(entry_id):
            panels.append(
                Panel(
                    "How others see them",
                    [
                        (
                            link(m.source_id),
                            index.name_of(m.source_id),
                            detail(m.details, "type", "feeling", "note"),
                        )
                        for m in index.incoming[entry_id]
                    ],
                )
            )
        sources: dict[str, int] = {}
        for ref in index.backlinks.get(entry_id, []):
            sources[ref.source_id] = sources.get(ref.source_id, 0) + 1
        mentioned = Panel(
            "Mentioned in",
            [
                (link(s), index.name_of(s), f"{n} link(s)")
                for s, n in sorted(sources.items(), key=lambda kv: index.name_of(kv[0]).casefold())
            ],
        )
        unlinked: dict[str, int] = {}
        for mention in index.mentions():
            if mention.target_id == entry_id and mention.source_id not in sources:
                unlinked[mention.source_id] = unlinked.get(mention.source_id, 0) + 1
        for source_id, count in sorted(unlinked.items(), key=lambda kv: index.name_of(kv[0])):
            mentioned.items.append(
                (link(source_id), index.name_of(source_id), f"{count} unlinked mention(s)")
            )
        if mentioned.items:
            panels.append(mentioned)
        return panels

    @app.get("/entry/{entry_id}", response_class=HTMLResponse)
    def entry_page(entry_id: str, ctx: CtxDep) -> HTMLResponse:
        view = ctx.view
        document = view.index.get(entry_id)
        if document is None:
            return not_found(ctx, "That entry")
        renderer = view.renderer
        meta = document.meta
        card_keys = list(QUICK_CARD.get(document.type, ()))
        image_html = None
        for key in IMAGE_FIELDS:
            if isinstance(meta.get(key), str):
                image_html = Markup(renderer.inline(meta[key], document.id))
                break
        detail_keys = [
            k
            for k in meta
            if k not in HIDDEN_DETAIL_FIELDS and k not in card_keys and k not in IMAGE_FIELDS
        ]
        if ctx.gm:
            party_view = meta.get("party_view")
            for key in party_view if isinstance(party_view, dict) else {}:
                if key not in card_keys and key not in detail_keys and key not in IMAGE_FIELDS:
                    detail_keys.append(key)
        parent_id = view.index.parent_of.get(document.id)
        aliases = [
            a if isinstance(a, str) else a.get("name")
            for a in meta.get("aliases") or []
            if isinstance(a, str) or (isinstance(a, dict) and not a.get("search_only"))
        ]
        return page(
            ctx,
            "entry.html",
            doc=document,
            type_label=TYPE_LABELS.get(document.type, document.type),
            title=Markup(renderer.inline(document.name, document.id)),
            aliases=[a for a in aliases if a],
            tags=meta.get("tags") or [],
            status=meta.get("status", "canon"),
            secret_entry=ctx.gm and is_secret_entry(meta),
            parent=(parent_id, view.index.name_of(parent_id)) if parent_id else None,
            image=image_html,
            card=rows_for(ctx, document, card_keys),
            details=rows_for(ctx, document, detail_keys),
            body=Markup(renderer.body(document)),
            panels=panels_for(ctx, document),
        )

    @app.get("/place/{owner_id}/{slug}", response_class=HTMLResponse)
    def place_page(owner_id: str, slug: str, ctx: CtxDep) -> HTMLResponse:
        place = ctx.view.index.places.get(f"{owner_id}#{slug}")
        owner = ctx.view.index.get(owner_id)
        if place is None or owner is None:
            return not_found(ctx, "That place")
        return page(
            ctx,
            "place.html",
            place=place,
            owner=owner,
            title=Markup(ctx.view.renderer.inline(place.name, owner_id)),
            body=Markup(ctx.view.renderer.place_body(owner, slug)),
        )

    # -- browse, search, GM tools -----------------------------------------------------------

    @app.get("/browse/{entry_type}", response_class=HTMLResponse)
    def browse(entry_type: str, ctx: CtxDep) -> HTMLResponse:
        if entry_type not in TYPE_LABELS:
            return not_found(ctx, "That type")
        index = ctx.view.index
        entries = (
            index.tree(entry_type)
            if entry_type in TREE_TYPES
            else [(d, 0) for d in index.of_type(entry_type)]
        )
        return page(
            ctx,
            "browse.html",
            entry_type=entry_type,
            label=TYPE_LABELS[entry_type],
            entries=entries,
        )

    @app.get("/search", response_class=HTMLResponse)
    def search(
        ctx: CtxDep,
        q: Annotated[str, Query(max_length=200)] = "",
        type: Annotated[str, Query(max_length=40)] = "",
    ) -> HTMLResponse:
        result = ctx.view.search.search(q, entry_type=type or None) if q.strip() else None
        template = "_results.html" if ctx.request.headers.get("HX-Request") else "search.html"
        return page(
            ctx, template, result=result, query=q, type_filter=type, type_labels=TYPE_LABELS
        )

    @app.get("/mismatches", response_class=HTMLResponse)
    def mismatches(ctx: CtxDep) -> HTMLResponse:
        if not ctx.gm:
            return not_found(ctx)
        renderer = ctx.view.renderer
        rows = [
            (
                m,
                Markup(renderer.value(m.truth, m.entry_id, m.field)),
                Markup(renderer.value(m.party_believes, m.entry_id, m.field)),
            )
            for m in find_mismatches(campaign.documents)
        ]
        return page(ctx, "mismatches.html", rows=rows)

    @app.get("/issues", response_class=HTMLResponse)
    def issues(ctx: CtxDep) -> HTMLResponse:
        if not ctx.gm:
            return not_found(ctx)
        return page(ctx, "issues.html", issues=validate(campaign))

    @app.get("/asset/{asset_id}")
    def asset(asset_id: str, ctx: CtxDep) -> Response:
        document = ctx.view.index.get(asset_id)
        if document is None or document.type != "asset":
            return not_found(ctx, "That asset")
        file_value = document.meta.get("file")
        path = campaign.repository.asset_file(file_value) if isinstance(file_value, str) else None
        if path is None:
            return not_found(ctx, "That asset file")
        return FileResponse(path)

    return app
