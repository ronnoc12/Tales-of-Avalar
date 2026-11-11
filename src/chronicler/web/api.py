"""The JSON API: GM only, every write is guarded (see ``deps.guard_write``).

* ``POST /api/changes``: the one way to change an entry (``dry_run`` checks without
  writing). The browser pages call the same service directly; this endpoint is for tools.
* ``POST /api/transform``: the editor toolbar and quick fixes (``edit_text.py``). The
  browser sends the body text and selection and applies the one edit it gets back.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from chronicler import edit_text
from chronicler.changes import Change, ChangeError, Outcome
from chronicler.serialize import EntryText
from chronicler.storage import StorageError
from chronicler.web.deps import GmDep, WriteGuard, changes_of

router = APIRouter(prefix="/api")


class ChangeBody(BaseModel):
    op: Literal["update", "create", "rename", "retire", "delete"]
    entry_id: str
    base_hash: str | None = None
    meta: dict[str, Any] | None = None
    fields: dict[str, Any] | None = None
    body: str | None = None
    entry_type: str | None = None
    name: str | None = None
    rename_mode: Literal["alias", "hard"] = "alias"
    dry_run: bool = False


class TransformBody(BaseModel):
    op: Literal[
        "secret",
        "prep",
        "unmark",
        "reveal",
        "heading-secret",
        "link",
        "embed",
        "close-section",
        "close-paragraph",
        "remove-tag",
    ]
    text: str
    start: int = 0  # selection, in UTF-16 code units (JavaScript string offsets)
    end: int = 0
    line: int = 0  # body line (0-based) and column, for the quick fixes
    column: int = 0
    target: str = ""  # reveal: "[[Session 7#Notes]]"; link: an entry name; embed: asset id
    asset_kind: str = "image"
    confirmed: bool = False


def outcome_json(outcome: Outcome) -> dict[str, Any]:
    return {
        "ok": outcome.ok,
        "applied": outcome.applied,
        "conflict": outcome.conflict,
        "conflict_path": outcome.conflict_path,
        "new_hash": outcome.new_hash,
        "errors": [asdict(i) for i in outcome.errors],
        "warnings": [asdict(i) for i in outcome.warnings],
        "files": [f.rel_path for f in outcome.files],
        "body_line": EntryText.parse(outcome.files[0].after).body_line
        if outcome.files and outcome.files[0].after
        else None,
    }


@router.post("/changes", dependencies=[WriteGuard])
def apply_change(ctx: GmDep, body: ChangeBody) -> JSONResponse:
    change = Change(
        body.op,
        body.entry_id,
        body.base_hash,
        meta=body.meta,
        fields=body.fields,
        body=body.body,
        entry_type=body.entry_type,
        name=body.name,
        rename_mode=body.rename_mode,
    )
    try:
        outcome = changes_of(ctx.request).apply(change, dry_run=body.dry_run)
    except ChangeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except StorageError as exc:
        return JSONResponse({"error": f"Could not save: {exc}"}, status_code=500)
    status = 409 if outcome.conflict else (422 if not outcome.ok and not body.dry_run else 200)
    return JSONResponse(jsonable_encoder(outcome_json(outcome)), status_code=status)


@router.post("/transform", dependencies=[WriteGuard])
def transform(ctx: GmDep, body: TransformBody) -> JSONResponse:
    text, a, b = body.text, body.start, body.end
    edits = {
        "secret": lambda: edit_text.wrap(text, a, b, "secret", confirmed=body.confirmed),
        "prep": lambda: edit_text.wrap(text, a, b, "prep"),
        "unmark": lambda: edit_text.unmark(text, a, b),
        "reveal": lambda: edit_text.reveal(text, a, b, body.target),
        "heading-secret": lambda: edit_text.heading_secret(text, a),
        "link": lambda: edit_text.insert_link(text, a, b, body.target),
        "embed": lambda: edit_text.insert_embed(text, a, b, body.target, body.asset_kind),
        "close-section": lambda: edit_text.close_unclosed(
            text, body.line, body.column, at="section"
        ),
        "close-paragraph": lambda: edit_text.close_unclosed(
            text, body.line, body.column, at="paragraph"
        ),
        "remove-tag": lambda: edit_text.remove_tag(text, body.line, body.column),
    }
    return JSONResponse(asdict(edits[body.op]()))
