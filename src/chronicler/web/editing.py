"""Editing pages (GM only), driven by HTMX: the server renders every piece of HTML.

* ``/edit/{id}``: edit mode. The form autosaves to ``/draft`` (2 s after typing stops,
  and every 10 s; the server skips it when nothing changed). Replies swap in the status,
  issues, and both previews.
* Row tools, rename / retire / delete / leave dialogs, the link / session / image picker.
* ``/edit/{id}/field/{key}``: quick edit of one field on the entry page.
* ``/new``: create an entry from its type's template.

Every request that changes something is JSON (htmx json-enc) with the X-Chronicler
header, and goes through :class:`~chronicler.changes.ChangeService`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import Markup

from chronicler.changes import Change, ChangeError, ChangeService, Outcome, RenameMode
from chronicler.document import Document, slugify
from chronicler.forms import (
    READONLY_KEYS,
    decode_field,
    decode_form,
    entry_form,
    field_spec,
    quick_spec,
)
from chronicler.schema import TYPE_LABELS
from chronicler.storage import StorageError
from chronicler.validate import Issue
from chronicler.web.accounts import safe_next
from chronicler.web.deps import Ctx, GmDep, WriteGuard, changes_of, page, templates

router = APIRouter()
FormBody = Annotated[dict[str, Any], Body()]
NAMES_LIMIT = 2000


# -- helpers --------------------------------------------------------------------------------------


def fragment(ctx: Ctx, template: str, status_code: int = 200, **extra: Any) -> HTMLResponse:
    return templates.TemplateResponse(
        ctx.request, template, ctx.base(**extra), status_code=status_code
    )


def hx(response: Response, **headers: str) -> Response:
    for name, value in headers.items():
        response.headers["HX-" + name.replace("_", "-").title()] = value
    return response


def redirect(url: str) -> Response:
    """Send the browser to ``url`` (same site only)."""
    return hx(Response(status_code=204), redirect=safe_next(url))


def dialog(ctx: Ctx, template: str, **extra: Any) -> Response:
    """A dialog, swapped into #modal-root wherever the request came from."""
    return hx(fragment(ctx, template, **extra), retarget="#modal-root", reswap="innerHTML")


def link_names(ctx: Ctx) -> list[str]:
    """Official names and real aliases (never search-only misspellings), for <datalist>."""
    names: set[str] = set()
    for document in ctx.campaign.gm.index.docs.values():
        names.add(document.name)
        for alias in document.meta.get("aliases") or []:
            if isinstance(alias, str):
                names.add(alias)
            elif isinstance(alias, dict) and alias.get("name") and not alias.get("search_only"):
                names.add(str(alias["name"]))
    return sorted(names, key=str.casefold)[:NAMES_LIMIT]


def _service(ctx: Ctx) -> ChangeService:
    return changes_of(ctx.request)


def _work(ctx: Ctx, entry_id: str, form: dict[str, Any]) -> tuple[dict[str, Any], str, list[str]]:
    """(meta, body, problems) from the edit-mode form."""
    state = _service(ctx).editor_state(entry_id)
    meta, problems = decode_form(state.meta, form)
    return meta, str(form.get("body", "")).replace("\r\n", "\n"), problems


def _base_hash(form: dict[str, Any]) -> str | None:
    value = form.get("base_hash")
    return str(value) if value else None


def _previews(ctx: Ctx, draft: Document) -> tuple[Markup, Markup | None]:
    """(GM, Player) renderings of the entry body as it would be after the edit."""
    others = [d for d in ctx.campaign.documents if d.id != draft.id]
    world = ctx.campaign.derive([*others, draft])
    gm_doc, player_doc = world.gm.index.get(draft.id), world.player.index.get(draft.id)
    gm = Markup(world.gm.renderer.body(gm_doc)) if gm_doc else Markup("")
    player = Markup(world.player.renderer.body(player_doc)) if player_doc else None
    return gm, player


def _issue_rows(issues: list[Issue], body_line: int) -> list[dict[str, Any]]:
    """Issues with their position in the body editor (0-based line), when they are there."""
    rows = []
    for issue in issues:
        index = issue.line - body_line if issue.line and issue.line >= body_line else None
        rows.append({"issue": issue, "body_index": index})
    return rows


def _feedback(
    ctx: Ctx,
    *,
    status: str,
    errors: list[Issue],
    warnings: list[Issue],
    body_line: int,
    draft: Document | None,
    problems: list[str] | None = None,
    has_draft: bool,
    base_hash: str | None = None,
) -> HTMLResponse:
    gm, player = _previews(ctx, draft) if draft is not None else (None, None)
    return fragment(
        ctx,
        "_feedback.html",
        status=status,
        errors=_issue_rows(errors, body_line),
        warnings=_issue_rows(warnings, body_line),
        problems=problems or [],
        preview_gm=gm,
        preview_player=player,
        show_previews=draft is not None,
        has_draft=has_draft,
        base_hash=base_hash,
    )


def _not_found(ctx: Ctx) -> HTMLResponse:
    return page(ctx, "not_found.html", status_code=404, what="That entry")


# -- edit mode ------------------------------------------------------------------------------------


@router.get("/edit/{entry_id}", response_class=HTMLResponse)
def edit_page(ctx: GmDep, entry_id: str) -> Response:
    if not ctx.gm:  # editing happens in the GM view
        return RedirectResponse(f"/view/gm?next=/edit/{entry_id}", status_code=303)
    service = _service(ctx)
    try:
        state = service.editor_state(entry_id)
    except ChangeError:
        return _not_found(ctx)
    errors, warnings, body_line, draft = service.check_edit(
        entry_id, meta=state.meta, body=state.body
    )
    gm, player = _previews(ctx, draft)
    document = ctx.view.index.docs[entry_id]
    return page(
        ctx,
        "edit.html",
        doc=document,
        state=state,
        specs=entry_form(state.meta),
        type_label=TYPE_LABELS.get(document.type, document.type),
        names=link_names(ctx),
        errors=_issue_rows(errors, body_line),
        warnings=_issue_rows(warnings, body_line),
        problems=[],
        preview_gm=gm,
        preview_player=player,
        show_previews=True,
        has_draft=state.from_draft,
        base_hash=state.base_hash,
    )


@router.post("/edit/{entry_id}/draft", dependencies=[WriteGuard])
def autosave(ctx: GmDep, entry_id: str, form: FormBody) -> Response:
    service = _service(ctx)
    try:
        meta, body, problems = _work(ctx, entry_id, form)
        check = service.autosave(entry_id, meta=meta, body=body, base_hash=_base_hash(form))
    except ChangeError as exc:
        return HTMLResponse(f'<p class="form-error">{Markup.escape(str(exc))}</p>', status_code=400)
    if check is None:
        return Response(status_code=204)  # nothing changed: nothing saved, nothing to swap
    _errors, _warnings, _line, draft = service.check_edit(entry_id, meta=meta, body=body)
    stale = (
        " The page changed on disk since you started; saving keeps a conflict copy."
        if check.stale
        else ""
    )
    return _feedback(
        ctx,
        status=f"Draft saved {check.info.saved_at.replace('T', ' ')} (not on the page yet).{stale}",
        errors=check.errors,
        warnings=check.warnings,
        body_line=check.body_line,
        draft=draft,
        problems=problems,
        has_draft=True,
    )


@router.post("/edit/{entry_id}/save", dependencies=[WriteGuard])
def save(ctx: GmDep, entry_id: str, form: FormBody, then: str = "") -> Response:
    service = _service(ctx)
    try:
        meta, body, problems = _work(ctx, entry_id, form)
        base_hash = _base_hash(form)
        service.autosave(entry_id, meta=meta, body=body, base_hash=base_hash)  # keep the work
        outcome = service.apply(Change("update", entry_id, base_hash, meta=meta, body=body))
    except ChangeError as exc:
        return dialog(ctx, "_dialog_message.html", title="Could not save", message=str(exc))
    except StorageError as exc:
        return dialog(ctx, "_dialog_message.html", title="Could not save", message=str(exc))
    if outcome.conflict:
        return dialog(ctx, "_dialog_conflict.html", entry_id=entry_id, path=outcome.conflict_path)
    if outcome.applied and then:
        return redirect(then)
    body_line, draft = _line_and_doc(service, entry_id, meta, body)
    if outcome.applied:
        status = "Saved to the page."
    else:
        status = "Not saved: fix the problems below. Your work is safe in the draft."
    return _feedback(
        ctx,
        status=status,
        errors=outcome.errors,
        warnings=outcome.warnings,
        body_line=body_line,
        draft=draft,
        problems=problems,
        has_draft=not outcome.applied,
        base_hash=outcome.new_hash,
    )


def _line_and_doc(
    service: ChangeService, entry_id: str, meta: dict[str, Any], body: str
) -> tuple[int, Document]:
    _errors, _warnings, body_line, draft = service.check_edit(entry_id, meta=meta, body=body)
    return body_line, draft


@router.post("/edit/{entry_id}/discard", dependencies=[WriteGuard])
def discard(ctx: GmDep, entry_id: str, then: str = "") -> Response:
    _service(ctx).drafts.discard(entry_id)
    return redirect(then or f"/edit/{entry_id}")


@router.post("/edit/{entry_id}/leave", dependencies=[WriteGuard])
def leave(ctx: GmDep, entry_id: str, form: FormBody, to: str = "/") -> Response:
    meta, body, _problems = _work(ctx, entry_id, form)
    if not _service(ctx).has_unsaved(entry_id, meta=meta, body=body):
        return redirect(to)
    return dialog(ctx, "_dialog_leave.html", entry_id=entry_id, to=to)


@router.post("/edit/{entry_id}/keep", dependencies=[WriteGuard])
def keep_in_draft(ctx: GmDep, entry_id: str, form: FormBody, then: str = "/") -> Response:
    meta, body, _problems = _work(ctx, entry_id, form)
    _service(ctx).autosave(entry_id, meta=meta, body=body, base_hash=_base_hash(form))
    return redirect(then)


# -- row tools ------------------------------------------------------------------------------------


@router.post("/edit/{entry_id}/rows", dependencies=[WriteGuard])
def rows(ctx: GmDep, entry_id: str, form: FormBody) -> Response:
    state = _service(ctx).editor_state(entry_id)
    key = str(form.get("field", ""))
    spec = field_spec(state.meta, key)
    if spec is None or spec.kind not in ("rows", "aliases") or key in READONLY_KEYS:
        return HTMLResponse("", status_code=400)
    value, _problems = decode_field(state.meta, form, key, keep_blank_rows=True)
    items: list[Any] = list(value) if isinstance(value, list) else []
    index = int(form.get("index", 0))
    op = str(form.get("op", ""))
    if op == "add":
        items.append({})
    elif op == "remove" and 0 <= index < len(items):
        items.pop(index)
    elif op == "up" and 0 < index < len(items):
        items[index - 1], items[index] = items[index], items[index - 1]
    elif op == "down" and 0 <= index < len(items) - 1:
        items[index + 1], items[index] = items[index], items[index + 1]
    data = {**state.meta, key: items}
    updated = field_spec(data, key)
    if updated is not None:
        updated.present = form.get(f"p:{key}") == "1"
    return fragment(ctx, "_field.html", spec=updated, doc=ctx.campaign.gm.index.docs[entry_id])


# -- rename, retire, delete -----------------------------------------------------------------------


def _needs_save(ctx: Ctx, entry_id: str, form: dict[str, Any], action: str) -> Response | None:
    meta, body, _problems = _work(ctx, entry_id, form)
    if _service(ctx).has_unsaved(entry_id, meta=meta, body=body):
        return dialog(ctx, "_dialog_save_first.html", entry_id=entry_id, action=action)
    return None


@router.post("/edit/{entry_id}/rename-dialog", dependencies=[WriteGuard])
def rename_dialog(ctx: GmDep, entry_id: str, form: FormBody) -> Response:
    blocked = _needs_save(ctx, entry_id, form, "Renaming")
    if blocked:
        return blocked
    doc = ctx.campaign.gm.index.docs[entry_id]
    return dialog(
        ctx, "_dialog_rename.html", doc=doc, base_hash=_service(ctx).current_hash(entry_id)
    )


def _rename_change(entry_id: str, form: dict[str, Any]) -> Change:
    mode: RenameMode = "hard" if form.get("mode") == "hard" else "alias"
    return Change(
        "rename", entry_id, _base_hash(form), name=str(form.get("name", "")), rename_mode=mode
    )


@router.post("/edit/{entry_id}/rename-preview", dependencies=[WriteGuard])
def rename_preview(ctx: GmDep, entry_id: str, form: FormBody) -> Response:
    doc = ctx.campaign.gm.index.docs[entry_id]
    try:
        outcome = _service(ctx).apply(_rename_change(entry_id, form), dry_run=True)
    except ChangeError as exc:
        return dialog(
            ctx,
            "_dialog_rename.html",
            doc=doc,
            base_hash=_base_hash(form),
            error=str(exc),
            name=form.get("name"),
        )
    return dialog(
        ctx,
        "_dialog_rename.html",
        doc=doc,
        base_hash=_base_hash(form),
        outcome=outcome,
        name=form.get("name"),
        mode=form.get("mode"),
    )


@router.post("/edit/{entry_id}/rename", dependencies=[WriteGuard])
def rename(ctx: GmDep, entry_id: str, form: FormBody) -> Response:
    return _apply_or_explain(
        ctx, _rename_change(entry_id, form), f"/edit/{entry_id}", "Not renamed"
    )


@router.post("/edit/{entry_id}/retire-dialog", dependencies=[WriteGuard])
def retire_dialog(ctx: GmDep, entry_id: str, form: FormBody) -> Response:
    blocked = _needs_save(ctx, entry_id, form, "Retiring")
    if blocked:
        return blocked
    return dialog(
        ctx,
        "_dialog_retire.html",
        entry_id=entry_id,
        base_hash=_service(ctx).current_hash(entry_id),
    )


@router.post("/edit/{entry_id}/retire", dependencies=[WriteGuard])
def retire(ctx: GmDep, entry_id: str, form: FormBody) -> Response:
    change = Change("retire", entry_id, _base_hash(form))
    return _apply_or_explain(ctx, change, f"/edit/{entry_id}", "Not retired")


@router.get("/edit/{entry_id}/delete-dialog", response_class=HTMLResponse)
def delete_dialog(ctx: GmDep, entry_id: str) -> Response:
    service = _service(ctx)
    try:
        preview = service.delete_preview(entry_id)
    except ChangeError:
        return HTMLResponse("", status_code=404)
    index = ctx.campaign.gm.index
    return dialog(
        ctx,
        "_dialog_delete.html",
        doc=index.docs[entry_id],
        preview=preview,
        backlinks=[(b, index.name_of(b)) for b in preview.backlinks],
        base_hash=service.current_hash(entry_id),
    )


@router.post("/edit/{entry_id}/delete", dependencies=[WriteGuard])
def delete(ctx: GmDep, entry_id: str, form: FormBody) -> Response:
    entry_type = ctx.campaign.gm.index.docs[entry_id].type
    change = Change("delete", entry_id, _base_hash(form))
    return _apply_or_explain(ctx, change, f"/browse/{entry_type}", "Not deleted")


def _apply_or_explain(ctx: Ctx, change: Change, success_url: str, title: str) -> Response:
    try:
        outcome: Outcome = _service(ctx).apply(change)
    except (ChangeError, StorageError) as exc:
        return dialog(ctx, "_dialog_message.html", title=title, message=str(exc))
    if outcome.applied:
        return redirect(success_url)
    if outcome.conflict:
        message = "The page changed on disk since you opened it. Reload and try again."
        return dialog(ctx, "_dialog_message.html", title=title, message=message, reload=True)
    return dialog(ctx, "_dialog_message.html", title=title, issues=outcome.errors)


# -- picker (link, reveal session, image) ---------------------------------------------------------


PICK_TYPES = {"link": None, "reveal": "session", "image": "asset"}


@router.get("/edit/pick/{tool}", response_class=HTMLResponse)
def picker(ctx: GmDep, tool: str) -> Response:
    if tool not in PICK_TYPES:
        return HTMLResponse("", status_code=404)
    return dialog(ctx, "_dialog_pick.html", tool=tool)


@router.get("/edit/pick/{tool}/results", response_class=HTMLResponse)
def picker_results(ctx: GmDep, tool: str, q: str = Query("", max_length=100)) -> Response:
    entry_type = PICK_TYPES.get(tool)
    query = q.strip().casefold()
    results: list[dict[str, str]] = []
    for document in sorted(ctx.campaign.gm.index.docs.values(), key=lambda d: d.name.casefold()):
        if entry_type and document.type != entry_type:
            continue
        if query and query not in document.name.casefold():
            continue
        results.append(_pick_item(tool, document, None))
        if tool == "reveal":
            results += [_pick_item(tool, document, s.title) for s in document.sections if s.level]
        if len(results) >= 40:
            break
    return fragment(ctx, "_pick_results.html", results=results)


def _pick_item(tool: str, document: Document, section: str | None) -> dict[str, str]:
    if tool == "reveal":
        target = f"[[{document.name}#{section}]]" if section else f"[[{document.name}]]"
        label = f"{document.name} › {section}" if section else document.name
        return {"target": target, "label": label, "kind": ""}
    if tool == "image":
        kind = str(document.meta.get("kind", "image"))
        return {"target": document.id, "label": document.name, "kind": kind}
    return {
        "target": document.name,
        "label": f"{document.name} ({TYPE_LABELS.get(document.type, document.type)})",
        "kind": "",
    }


# -- quick edit (entry page) ----------------------------------------------------------------------


def _quick(ctx: Ctx, entry_id: str, key: str, **extra: Any) -> Response:
    document = ctx.campaign.gm.index.get(entry_id)
    spec = quick_spec(document.meta, key) if document else None
    if document is None or spec is None:
        return HTMLResponse("", status_code=404)
    return fragment(
        ctx,
        "_quick_field.html",
        doc=document,
        spec=spec,
        base_hash=_service(ctx).current_hash(entry_id),
        names=link_names(ctx) if spec.kind == "link" else [],
        **extra,
    )


@router.get("/edit/{entry_id}/field/{key}", response_class=HTMLResponse)
def quick_field(ctx: GmDep, entry_id: str, key: str) -> Response:
    return _quick(ctx, entry_id, key)


@router.post("/edit/{entry_id}/field/{key}", dependencies=[WriteGuard])
def quick_save(ctx: GmDep, entry_id: str, key: str, form: FormBody) -> Response:
    document = ctx.campaign.gm.index.get(entry_id)
    if document is None or quick_spec(document.meta, key) is None:
        return HTMLResponse("", status_code=404)
    value, problems = decode_field(document.meta, form, key)
    change = Change("update", entry_id, _base_hash(form), fields={key: value})
    try:
        outcome = _service(ctx).apply(change)
    except ChangeError as exc:
        return _quick(ctx, entry_id, key, error=str(exc))
    if outcome.applied:
        return hx(Response(status_code=204), refresh="true")
    if outcome.conflict:
        return _quick(ctx, entry_id, key, conflict=True)
    return _quick(ctx, entry_id, key, issues=outcome.errors, problems=problems)


# -- new entry ------------------------------------------------------------------------------------


@router.get("/new", response_class=HTMLResponse)
def new_entry_page(ctx: GmDep, type: str = Query("npc", max_length=40)) -> Response:
    entry_type = type if type in TYPE_LABELS else "npc"
    return page(ctx, "new_entry.html", entry_type=entry_type)


@router.get("/new/id-hint", response_class=HTMLResponse)
def new_entry_hint(
    ctx: GmDep,
    name: str = Query("", max_length=200),
    party_name: str = Query("", max_length=200),
    hidden_name: str = Query(""),
) -> Response:
    source = party_name if hidden_name and party_name.strip() else name
    suggestion = slugify(source) if source.strip() else ""
    return fragment(ctx, "_id_hint.html", suggestion=suggestion)


@router.post("/new", dependencies=[WriteGuard])
def create(ctx: GmDep, form: FormBody) -> Response:
    name = str(form.get("name", ""))
    party_name = str(form.get("party_name", "")).strip()
    hidden = bool(form.get("hidden_name")) and bool(party_name)
    entry_id = str(form.get("entry_id", "")).strip() or slugify(party_name if hidden else name)
    change = Change(
        "create",
        entry_id,
        entry_type=str(form.get("entry_type", "")),
        name=name,
        meta={"party_view": {"name": party_name}} if hidden else None,
    )
    try:
        outcome = _service(ctx).apply(change)
    except (ChangeError, StorageError) as exc:
        return fragment(ctx, "_form_error.html", message=str(exc))
    if outcome.applied:
        return redirect(f"/edit/{entry_id}")
    return fragment(ctx, "_form_error.html", issues=outcome.errors)
