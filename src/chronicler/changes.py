"""Changes: check proposed edits against the whole world, then write them.

Every edit made in the browser goes through :meth:`ChangeService.apply`:

1. **plan** the new file texts (nothing is written yet);
2. **conflict check**: if the entry file changed on disk since editing started (its
   content fingerprint differs), the work is kept as a conflict draft instead;
3. **validate** the world *with the proposed files swapped in*, reusing ``validate.py``:
   errors in the files being written block the save; anything newly broken elsewhere
   (for example, links to a deleted entry) is reported as a warning;
4. **write** through storage (all files or none), then reload the campaign.

A dry run stops after step 3. This module is the only code path that edits entries.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Literal

from pydantic import ValidationError

from chronicler.campaign import Campaign
from chronicler.document import Document, parse_document
from chronicler.drafts import DraftInfo, DraftStore
from chronicler.schema import ENTRY_MODELS, ID_PATTERN, TYPE_FOLDERS
from chronicler.serialize import EntryText, SerializeError, new_entry_text, update_entry_text
from chronicler.storage import StorageError
from chronicler.validate import Issue, validate

Op = Literal["update", "create", "rename", "retire", "delete"]
RenameMode = Literal["alias", "hard"]
_ID_RE = re.compile(ID_PATTERN)


class ChangeError(ValueError):
    """The request itself is invalid (unknown entry, bad id, …); nothing was checked."""


@dataclass
class Change:
    op: Op
    entry_id: str
    base_hash: str | None = None  # entry file fingerprint when editing started
    meta: dict[str, Any] | None = None  # update / create: the complete new frontmatter
    fields: dict[str, Any] | None = None  # update: only these top-level keys (quick edit)
    body: str | None = None  # update / create: the new body text
    entry_type: str | None = None  # create
    name: str | None = None  # create, rename
    rename_mode: RenameMode = "alias"


@dataclass(frozen=True)
class FileChange:
    rel_path: str
    entry_id: str | None
    before: str | None  # None: a new file
    after: str | None  # None: the file is deleted


@dataclass
class Outcome:
    ok: bool
    applied: bool = False
    files: list[FileChange] = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    conflict: bool = False
    conflict_path: str | None = None
    new_hash: str | None = None


@dataclass(frozen=True)
class EditorState:
    entry_id: str
    rel_path: str
    meta: dict[str, Any]
    body: str
    base_hash: str | None
    from_draft: bool
    stale: bool  # the draft was started from an older version of the file
    saved_at: str | None = None


@dataclass(frozen=True)
class DraftCheck:
    """What autosave reports back to the editor."""

    info: DraftInfo
    errors: list[Issue]
    warnings: list[Issue]
    body_line: int  # file line number where the body starts (to place issues in the editor)
    stale: bool  # the entry file changed since this draft was started


@dataclass(frozen=True)
class DeletePreview:
    backlinks: list[str]  # entry ids whose links will break
    drafts: list[str]  # draft files that will be removed
    uncommitted: bool | None  # None: git status unknown


# -- value helpers ---------------------------------------------------------------------------


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def prune(value: Any) -> Any:
    """Drop empty values from mappings (and empty items from lists), recursively."""
    if isinstance(value, Mapping):
        pruned = {str(k): prune(v) for k, v in value.items()}
        return {k: v for k, v in pruned.items() if not _is_empty(v)}
    if isinstance(value, list):
        return [item for item in (prune(v) for v in value) if not _is_empty(item)]
    return value


def _loose(value: Any) -> Any:
    """For "was this touched?": every kind of empty (no value, "", [], {}) reads the same
    in a form, so an untouched template placeholder such as ``map_pin:`` stays as it is."""
    if _is_empty(value):
        return None
    if isinstance(value, Mapping):
        loose = {str(k): _loose(v) for k, v in value.items()}
        return {k: v for k, v in loose.items() if v is not None} or None
    if isinstance(value, list):
        return [_loose(v) for v in value]
    return value


def merge_meta(current: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """New frontmatter from an edit form: untouched keys stay exactly as they were
    (even empty template placeholders such as ``motto: ""``); changed keys are pruned,
    and dropped if empty."""
    result: dict[str, Any] = {}
    for key, value in new.items():
        if key in current and _loose(current[key]) == _loose(value):
            result[key] = current[key]
            continue
        pruned = prune(value)
        if not _is_empty(pruned):
            result[key] = pruned
    return result


def apply_fields(current: Mapping[str, Any], fields: Mapping[str, Any]) -> dict[str, Any]:
    """Quick edit: replace (or, when empty, remove) only the given top-level keys."""
    result = dict(current)
    for key, value in fields.items():
        pruned = prune(value)
        if _is_empty(pruned):
            result.pop(key, None)
        else:
            result[key] = pruned
    return result


def _decimals_like(meta: Any, typed: Any) -> Any:
    if (
        isinstance(typed, Decimal)
        and isinstance(meta, int | float | str)
        and not isinstance(meta, bool)
    ):
        return typed
    if isinstance(meta, dict) and isinstance(typed, dict):
        return {k: _decimals_like(v, typed.get(k)) for k, v in meta.items()}
    if isinstance(meta, list) and isinstance(typed, list) and len(meta) == len(typed):
        return [_decimals_like(m, t) for m, t in zip(meta, typed, strict=True)]
    return meta


def normalize_numbers(meta: dict[str, Any]) -> dict[str, Any]:
    """Scores and other decimals as the schema reads them (``2.5`` -> ``2.50``)."""
    model = ENTRY_MODELS.get(str(meta.get("type", "")))
    if model is None:
        return meta
    try:
        typed = model.model_validate(meta).model_dump(by_alias=True)
    except ValidationError:
        return meta  # the validator will report the problem
    result = _decimals_like(meta, typed)
    return result if isinstance(result, dict) else meta


def _relinker(old_name: str, new_name: str) -> Callable[[str], str]:
    """Rewrites ``[[Old Name…]]`` links (any section or label) to ``[[New Name…]]``."""
    pattern = re.compile(r"(\[\[)\s*" + re.escape(old_name) + r"\s*(?=[#|\]])", re.IGNORECASE)

    def relink(source: str) -> str:
        return pattern.sub(lambda m: m.group(1) + new_name, source)

    return relink


def _issue_key(issue: Issue) -> tuple[Any, ...]:
    return (issue.level, issue.code, issue.file, issue.message, issue.field)


def _as_warning(issue: Issue) -> Issue:
    return replace(issue, level="warning")


# -- the service -----------------------------------------------------------------------------


class ChangeService:
    def __init__(self, campaign: Campaign) -> None:
        self.campaign = campaign
        self.repository = campaign.repository
        self.drafts = DraftStore(campaign.repository)
        self._lock = threading.RLock()
        self._baseline: tuple[Any, set[tuple[Any, ...]]] | None = None

    # -- reading ------------------------------------------------------------------------

    def _document(self, entry_id: str) -> Document:
        self.campaign.refresh_if_changed()
        document = self.campaign.gm.index.get(entry_id)
        if document is None:
            raise ChangeError(f"no entry with id {entry_id!r}")
        return document

    def current_hash(self, entry_id: str) -> str | None:
        return self.repository.entry_hash(self._document(entry_id).rel_path)

    def editor_state(self, entry_id: str) -> EditorState:
        """What edit mode starts from: the entry's draft if there is one, else the file."""
        document = self._document(entry_id)
        text = self.repository.read_entry(document.rel_path)
        current_hash = self.repository.entry_hash(document.rel_path)
        draft = self.drafts.get(entry_id)
        source, base_hash = (draft.text, draft.info.base_hash) if draft else (text, current_hash)
        try:
            body = EntryText.parse(source).body
        except SerializeError as exc:
            raise ChangeError(f"{document.rel_path} cannot be edited here: {exc}") from exc
        return EditorState(
            entry_id,
            document.rel_path,
            parse_document(source, document.rel_path).meta,
            body,
            base_hash,
            from_draft=draft is not None,
            stale=draft is not None and draft.info.base_hash != current_hash,
            saved_at=draft.info.saved_at if draft else None,
        )

    def delete_preview(self, entry_id: str) -> DeletePreview:
        document = self._document(entry_id)
        sources = {r.source_id for r in self.campaign.gm.index.backlinks.get(entry_id, [])}
        return DeletePreview(
            sorted(sources),
            [p.name for p in self.drafts.files_for(entry_id)],
            self.repository.uncommitted(document.rel_path),
        )

    # -- planning -----------------------------------------------------------------------

    def _edit_text(self, document: Document, text: str, change: Change) -> str:
        current = parse_document(text, document.rel_path).meta
        if change.fields is not None:
            meta = apply_fields(current, change.fields)
        elif change.meta is not None:
            meta = merge_meta(current, change.meta)
        else:
            meta = dict(current)
        if str(meta.get("id", "")) != document.id:
            raise ChangeError("an entry's id never changes")
        if str(meta.get("type", "")) != document.type:
            raise ChangeError("an entry's type cannot change here")
        try:
            return update_entry_text(text, meta=normalize_numbers(meta), body=change.body)
        except SerializeError as exc:
            raise ChangeError(f"{document.rel_path} cannot be edited here: {exc}") from exc

    def plan(self, change: Change) -> list[FileChange]:
        if change.op == "create":
            return [self._plan_create(change)]
        document = self._document(change.entry_id)
        rel = document.rel_path
        text = self.repository.read_entry(rel)
        if change.op == "delete":
            return [FileChange(rel, document.id, text, None)]
        if change.op == "retire":
            edit = Change("update", document.id, fields={"status": "retired"})
            return [FileChange(rel, document.id, text, self._edit_text(document, text, edit))]
        if change.op == "rename":
            return self._plan_rename(document, text, change)
        return [FileChange(rel, document.id, text, self._edit_text(document, text, change))]

    def _plan_create(self, change: Change) -> FileChange:
        entry_type = change.entry_type or ""
        name = (change.name or "").strip()
        if entry_type not in ENTRY_MODELS:
            raise ChangeError(f"unknown entry type {entry_type!r}")
        if not name:
            raise ChangeError("a new entry needs a name")
        if not _ID_RE.match(change.entry_id):
            raise ChangeError("ids are lowercase letters, digits, and dashes")
        if self.campaign.gm.index.get(change.entry_id) is not None:
            raise ChangeError(f"the id {change.entry_id!r} is already used")
        rel = f"{TYPE_FOLDERS[entry_type]}/{change.entry_id}.md"
        if self.repository.entry_path(rel).exists():
            raise ChangeError(f"{rel} already exists")
        basics = {"id": change.entry_id, "type": entry_type, "name": name}
        extra = change.meta or {}
        template = self.repository.template_text(entry_type)
        if template is None:
            text = new_entry_text({**extra, **basics}, change.body or "")
            return FileChange(rel, change.entry_id, None, text)
        meta = {**parse_document(template, rel).meta, **extra, **basics}  # basics always win
        text = update_entry_text(template, meta=meta, body=change.body)
        return FileChange(rel, change.entry_id, None, text)

    def _plan_rename(self, document: Document, text: str, change: Change) -> list[FileChange]:
        new_name = (change.name or "").strip()
        old_name = document.name
        if not new_name or new_name == old_name:
            raise ChangeError("give the entry a different name")
        if change.rename_mode == "alias":
            meta = dict(parse_document(text, document.rel_path).meta)
            aliases = list(meta.get("aliases") or [])
            known = {a if isinstance(a, str) else str(a.get("name", "")) for a in aliases}
            if old_name not in known:
                aliases.append(old_name)
            meta.update(name=new_name, aliases=aliases)
            after = self._edit_text(document, text, Change("update", document.id, meta=meta))
            return [FileChange(document.rel_path, document.id, text, after)]
        # Hard rename: rewrite every [[Old Name…]] link, in this file and all others.
        shared = {name.casefold() for name, _ids in self.campaign.gm.index.shared_names()}
        if old_name.casefold() in shared:
            raise ChangeError(
                f"{old_name!r} is also used by another entry; rename with an alias instead"
            )
        relink = _relinker(old_name, new_name)
        files: list[FileChange] = []
        for other in self.campaign.documents:
            if other.id == document.id or not other.id:
                continue
            before = self.repository.read_entry(other.rel_path)
            after = relink(before)
            if after != before:
                files.append(FileChange(other.rel_path, other.id, before, after))
        own = relink(text)
        meta = dict(parse_document(own, document.rel_path).meta)
        meta["name"] = new_name
        after = self._edit_text(document, own, Change("update", document.id, meta=meta))
        files.insert(0, FileChange(document.rel_path, document.id, text, after))
        return files

    # -- checking -----------------------------------------------------------------------

    def _baseline_issues(self) -> set[tuple[Any, ...]]:
        fingerprint = self.campaign.fingerprint
        if self._baseline is None or self._baseline[0] != fingerprint:
            self._baseline = (fingerprint, {_issue_key(i) for i in validate(self.campaign)})
        return self._baseline[1]

    def check(self, files: list[FileChange]) -> tuple[list[Issue], list[Issue]]:
        """(errors that block, warnings) for the world with ``files`` applied."""
        touched = {f.rel_path for f in files}
        documents = [d for d in self.campaign.documents if d.rel_path not in touched]
        documents += [parse_document(f.after, f.rel_path) for f in files if f.after is not None]
        issues = validate(self.campaign.derive(documents))
        written = {f.rel_path for f in files if f.after is not None}
        baseline = self._baseline_issues()
        errors: list[Issue] = []
        warnings: list[Issue] = []
        for issue in issues:
            if issue.file in written:
                (errors if issue.level == "error" else warnings).append(issue)
            elif _issue_key(issue) not in baseline:
                warnings.append(_as_warning(issue))  # newly broken elsewhere: tell, never block
        return errors, warnings

    # -- applying -----------------------------------------------------------------------

    def apply(self, change: Change, *, dry_run: bool = False) -> Outcome:
        with self._lock:
            if change.op != "create" and change.base_hash is None:
                raise ChangeError("missing base_hash: reload the page and try again")
            files = self.plan(change)
            primary = files[0]
            if change.op != "create":
                current = self.repository.entry_hash(primary.rel_path)
                if current != change.base_hash:
                    return self._conflict(change, primary, dry_run=dry_run)
            errors, warnings = self.check(files)
            outcome = Outcome(not errors, files=files, errors=errors, warnings=warnings)
            if errors or dry_run:
                return outcome
            self._write(files)
            self.campaign.reload()
            outcome.applied = True
            if change.op == "delete":
                self.drafts.delete_all(change.entry_id)
                return outcome
            outcome.new_hash = self.repository.entry_hash(primary.rel_path)
            self._after_save(change, primary, outcome.new_hash)
            return outcome

    def _conflict(self, change: Change, primary: FileChange, *, dry_run: bool) -> Outcome:
        """Keep edit-mode work as a conflict copy. A refused quick edit or rename keeps
        nothing (the page reloads); it never touches the open draft."""
        outcome = Outcome(False, conflict=True)
        from_edit_mode = change.meta is not None or change.body is not None
        if not dry_run and primary.after is not None and change.op == "update" and from_edit_mode:
            path = self.drafts.move_to_conflicts(change.entry_id, primary.after)
            outcome.conflict_path = path.relative_to(self.repository.root).as_posix()
        return outcome

    def _write(self, files: list[FileChange]) -> None:
        """Write every file or none: on failure, restore what was already written."""
        done: list[FileChange] = []
        try:
            for change in files:
                if change.after is None:
                    self.repository.delete_entry(change.rel_path)
                else:
                    self.repository.write_entry(change.rel_path, change.after)
                done.append(change)
        except StorageError:
            for change in reversed(done):
                if change.before is None:
                    self.repository.delete_entry(change.rel_path)
                else:
                    self.repository.write_entry(change.rel_path, change.before)
            raise

    def _after_save(self, change: Change, primary: FileChange, new_hash: str | None) -> None:
        """Keep an open edit-mode draft consistent with what was just saved.

        A full edit-mode save *is* the draft, so it is discarded. Anything else (quick edit,
        rename, retire) applies the same field changes to the draft, so saving the draft
        later can never quietly undo them; a hard rename also rewrites the draft's links.
        """
        if change.op == "create" or primary.before is None or primary.after is None:
            return
        if change.op == "update" and (change.meta is not None or change.body is not None):
            self.drafts.discard(change.entry_id)
            return
        before = parse_document(primary.before, primary.rel_path).meta
        after = parse_document(primary.after, primary.rel_path).meta
        fields = {
            key: after.get(key)
            for key in [*before, *[k for k in after if k not in before]]
            if before.get(key) != after.get(key)
        }
        relink = None
        if change.op == "rename" and change.rename_mode == "hard":
            relink = _relinker(str(before.get("name", "")), str(after.get("name", "")))
        self._patch_draft(change.entry_id, fields, new_hash, relink)

    def _patch_draft(
        self,
        entry_id: str,
        fields: dict[str, Any],
        new_hash: str | None,
        transform: Callable[[str], str] | None = None,
    ) -> None:
        draft = self.drafts.get(entry_id)
        if draft is None:
            return
        source = transform(draft.text) if transform else draft.text
        meta = apply_fields(parse_document(source, draft.info.rel_path).meta, fields)
        text = update_entry_text(source, meta=normalize_numbers(meta))
        self.drafts.save(entry_id, draft.info.rel_path, text, new_hash)

    # -- drafts -------------------------------------------------------------------------

    def _draft_text(
        self, entry_id: str, meta: dict[str, Any], body: str
    ) -> tuple[Document, str, str]:
        """(document, current file text, the text this edit would produce)."""
        document = self._document(entry_id)
        text = self.repository.read_entry(document.rel_path)
        edit = Change("update", entry_id, meta=meta, body=body)
        return document, text, self._edit_text(document, text, edit)

    def save_draft(
        self, entry_id: str, *, meta: dict[str, Any], body: str, base_hash: str | None
    ) -> DraftCheck:
        """Autosave: store the work, then report what a save would say about it."""
        with self._lock:
            document, text, draft_text = self._draft_text(entry_id, meta, body)
            info = self.drafts.save(entry_id, document.rel_path, draft_text, base_hash)
            errors, warnings = self.check(
                [FileChange(document.rel_path, entry_id, text, draft_text)]
            )
            return DraftCheck(
                info,
                errors,
                warnings,
                body_line=EntryText.parse(draft_text).body_line,
                stale=self.repository.entry_hash(document.rel_path) != base_hash,
            )

    def autosave(
        self, entry_id: str, *, meta: dict[str, Any], body: str, base_hash: str | None
    ) -> DraftCheck | None:
        """Save a draft only if the work differs from the last draft (or the file);
        ``None`` means nothing changed, so nothing was written or checked."""
        with self._lock:
            _document, text, draft_text = self._draft_text(entry_id, meta, body)
            draft = self.drafts.get(entry_id)
            if draft_text == (draft.text if draft else text):
                return None
            return self.save_draft(entry_id, meta=meta, body=body, base_hash=base_hash)

    def check_edit(
        self, entry_id: str, *, meta: dict[str, Any], body: str
    ) -> tuple[list[Issue], list[Issue], int, Document]:
        """(errors, warnings, body line, document) for an edit, without saving anything."""
        document, text, draft_text = self._draft_text(entry_id, meta, body)
        errors, warnings = self.check([FileChange(document.rel_path, entry_id, text, draft_text)])
        draft_doc = parse_document(draft_text, document.rel_path)
        return errors, warnings, EntryText.parse(draft_text).body_line, draft_doc

    def has_unsaved(self, entry_id: str, *, meta: dict[str, Any], body: str) -> bool:
        """Is there work that is not on the page yet (a draft, or edits not autosaved)?"""
        _document, text, draft_text = self._draft_text(entry_id, meta, body)
        return self.drafts.get(entry_id) is not None or draft_text != text

    def draft_document(self, entry_id: str, *, meta: dict[str, Any], body: str) -> Document:
        """The entry as it would be after this edit (for previews)."""
        document, _text, draft_text = self._draft_text(entry_id, meta, body)
        return parse_document(draft_text, document.rel_path)
