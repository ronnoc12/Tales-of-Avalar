"""``chronicler validate``: find every problem in a campaign, with a fix hint where possible."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from chronicler.calendar import CalendarError
from chronicler.campaign import Campaign
from chronicler.document import Document, Section, slugify
from chronicler.index import CampaignIndex, walk_strings
from chronicler.markup import Node, check_attrs, iter_spans, parse_line, parse_text
from chronicler.schema import (
    DATE_FIELDS,
    ENTRY_MODELS,
    FUZZY_DATE_FIELDS,
    TYPE_FOLDERS,
    known_fields,
    nested_unknown_fields,
)

Level = Literal["error", "warning"]
NON_CANON_TARGETS = ("retired", "idea")


@dataclass(frozen=True)
class Issue:
    level: Level
    code: str
    message: str
    file: str
    line: int | None = None
    entry_id: str | None = None
    field: str | None = None
    column: int | None = None  # 0-based, for markup issues in the body

    def format(self) -> str:
        where = self.file + (f":{self.line}" if self.line else "")
        detail = f" [{self.field}]" if self.field else ""
        return f"{where}: {self.level}: {self.message}{detail} ({self.code})"


def _loc(loc: tuple[int | str, ...]) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _values_at(meta: Any, path: tuple[str, ...], trail: str = "") -> Iterator[tuple[str, Any]]:
    if not path:
        yield trail, meta
        return
    head, rest = path[0], path[1:]
    if head == "*":
        if isinstance(meta, list):
            for i, item in enumerate(meta):
                yield from _values_at(item, rest, f"{trail}[{i}]")
    elif isinstance(meta, dict) and head in meta:
        yield from _values_at(meta[head], rest, f"{trail}.{head}" if trail else head)


class Validator:
    def __init__(self, campaign: Campaign) -> None:
        self.campaign = campaign
        self.index: CampaignIndex = campaign.gm.index
        self.issues: list[Issue] = []

    def add(
        self,
        level: Level,
        code: str,
        message: str,
        document: Document | None = None,
        *,
        file: str | None = None,
        line: int | None = None,
        field: str | None = None,
        column: int | None = None,
    ) -> None:
        self.issues.append(
            Issue(
                level,
                code,
                message,
                file or (document.rel_path if document else ""),
                line,
                document.id if document else None,
                field,
                column,
            )
        )

    def run(self) -> list[Issue]:
        for config_issue in self.campaign.config_issues:
            self.add("error", "config", config_issue.message, file=config_issue.file)
        for document in self.campaign.documents:
            self._document(document)
        self._duplicates()
        self._references()
        self._hidden_names()
        self._revealing_ids()
        self._parents()
        return sorted(self.issues, key=lambda i: (i.file, i.line or 0, i.code))

    # -- per document -------------------------------------------------------------------

    def _document(self, document: Document) -> None:
        for issue in document.issues:
            self.add("error", issue.code, issue.message, document, line=issue.line)
        meta = document.meta
        if not meta:
            return
        entry_type = str(meta.get("type") or "")
        if not entry_type:
            self.add("error", "missing-type", "entry has no 'type'", document)
            return
        model = ENTRY_MODELS.get(entry_type)
        if model is None:
            self.add(
                "error",
                "unknown-type",
                f"unknown type {entry_type!r}; use one of: {', '.join(ENTRY_MODELS)}",
                document,
            )
            return
        folder = document.rel_path.split("/", 1)[0] if "/" in document.rel_path else ""
        if folder != TYPE_FOLDERS[entry_type]:
            self.add(
                "warning",
                "wrong-folder",
                f"{entry_type} entries belong in '{TYPE_FOLDERS[entry_type]}/'",
                document,
            )
        try:
            validated = model.model_validate(meta)
        except ValidationError as exc:
            for error in exc.errors():
                self.add("error", "schema", error["msg"], document, field=_loc(error["loc"]))
        else:
            for path in nested_unknown_fields(validated):
                parent, _, key = path.rpartition(".")
                self.add(
                    "warning",
                    "unknown-field",
                    f"unknown field {key!r} in {parent} (an unquoted comma in YAML splits "
                    'text into extra keys: put the value in "quotes")',
                    document,
                    field=path,
                )
        fields = known_fields(entry_type)
        for key in meta:
            if key not in fields:
                self.add("warning", "unknown-field", f"unknown field {key!r}", document, field=key)
        secret_fields = meta.get("secret_fields")
        for key in secret_fields if isinstance(secret_fields, list) else []:
            if key not in meta:
                self.add(
                    "warning",
                    "secret-field-missing",
                    f"secret_fields names {key!r}, which this entry does not have",
                    document,
                )
        party_view = meta.get("party_view")
        for key in party_view if isinstance(party_view, dict) else {}:
            if key not in fields:
                self.add(
                    "warning",
                    "unknown-field",
                    f"party_view sets unknown field {key!r}",
                    document,
                    field=f"party_view.{key}",
                )
        self._markup(document)
        self._dates(document, entry_type)
        if entry_type == "asset":
            file_value = meta.get("file")
            if (
                isinstance(file_value, str)
                and self.campaign.repository.asset_file(file_value) is None
            ):
                self.add(
                    "error",
                    "asset-missing",
                    f"asset file not found: assets/files/{file_value}",
                    document,
                    field="file",
                )

    def _markup(self, document: Document) -> None:
        for path, value in walk_strings(document.meta, ""):
            for nodes, issues in parse_text(value):
                for issue in issues:
                    self.add(issue.level, issue.code, issue.message, document, field=path)
                self._about_links(document, nodes, field=path)
        for section in document.sections:
            if section.level:
                nodes, issues = parse_line(section.title)
                for issue in issues:
                    self.add(issue.level, issue.code, issue.message, document, line=section.line)
                self._heading_attrs(document, section)
            for i, issue in section.markup_issues:
                line = section.content_line(i)
                self.add(
                    issue.level, issue.code, issue.message, document, line=line, column=issue.column
                )
            for i, line_text in enumerate(section.lines):
                if section.code[i]:
                    continue
                nodes, issues = parse_line(line_text)
                for issue in issues:
                    self.add(
                        issue.level,
                        issue.code,
                        issue.message,
                        document,
                        line=section.content_line(i),
                        column=issue.column,
                    )
                self._about_links(document, nodes, line=section.content_line(i))

    def _about_links(
        self,
        document: Document,
        nodes: list[Node],
        *,
        field: str | None = None,
        line: int | None = None,
    ) -> None:
        """``about=[[...]]`` on a span must point at a real entry."""
        for span in iter_spans(nodes):
            about = span.attrs.get("about")
            if span.continued or not about or not about.startswith("[["):
                continue
            if not self.index.resolve(about, source_id=document.id).ok:
                self.add(
                    "error",
                    "broken-link",
                    f"about={about} matches nothing",
                    document,
                    line=line,
                    field=field,
                )

    def _heading_attrs(self, document: Document, section: Section) -> None:
        for attr, args in section.attr_args.items():
            for issue in check_attrs(attr, args, 0):
                self.add(issue.level, issue.code, issue.message, document, line=section.line)
            about = args.get("about")
            if attr == "secret" and about and not self.index.resolve(about).ok:
                self.add(
                    "error",
                    "broken-link",
                    f"about={about} matches nothing",
                    document,
                    line=section.line,
                )

    def _dates(self, document: Document, entry_type: str) -> None:
        calendar = self.campaign.calendar
        for path in DATE_FIELDS.get(entry_type, ()):
            for field, value in _values_at(document.meta, path):
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                try:
                    calendar.parse(str(value))
                except CalendarError as exc:
                    self.add("error", "bad-date", str(exc), document, field=field)
        for path in FUZZY_DATE_FIELDS.get(entry_type, ()):
            for field, value in _values_at(document.meta, path):
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                try:
                    calendar.parse_fuzzy(str(value))
                except CalendarError as exc:
                    self.add("error", "bad-date", str(exc), document, field=field)

    # -- cross-entry ----------------------------------------------------------------------

    def _duplicates(self) -> None:
        for entry_id, paths in self.index.duplicate_ids.items():
            for path in paths[1:]:
                self.add(
                    "error",
                    "duplicate-id",
                    f"id {entry_id!r} is already used by {paths[0]}",
                    file=path,
                )
        for name, entries in self.index.shared_names():
            self.add(
                "warning",
                "duplicate-name",
                f"name/alias {name!r} is shared by: {', '.join(entries)}; "
                "links using it are ambiguous",
                file=self.index.docs[entries[0]].rel_path,
            )

    def _references(self) -> None:
        for reference in self.index.references:
            document = self.index.docs[reference.source_id]
            resolution = reference.resolution
            if resolution.status == "missing":
                hint = ""
                if resolution.suggestions:
                    hint = " (did you mean: " + ", ".join(resolution.suggestions) + "?)"
                self.add(
                    "error",
                    "broken-link",
                    f"link {reference.raw} matches nothing{hint}",
                    document,
                    line=reference.line,
                    field=reference.field,
                )
            elif resolution.status == "ambiguous":
                self.add(
                    "error",
                    "ambiguous-link",
                    f"link {reference.raw} could mean: {', '.join(resolution.candidates)}",
                    document,
                    line=reference.line,
                    field=reference.field,
                )
            elif resolution.status == "missing-section":
                self.add(
                    "warning",
                    "missing-section",
                    f"link {reference.raw}: no such section",
                    document,
                    line=reference.line,
                    field=reference.field,
                )
            elif resolution.entry_id and document.meta.get("status", "canon") == "canon":
                target = self.index.docs[resolution.entry_id]
                target_status = target.meta.get("status", "canon")
                if target_status in NON_CANON_TARGETS:
                    self.add(
                        "warning",
                        "links-non-canon",
                        f"canon entry links to {target.name!r}, which is {target_status}",
                        document,
                        line=reference.line,
                        field=reference.field,
                    )
        for embed in self.index.embeds:
            document = self.index.docs[embed.source_id]
            asset = self.index.get(embed.ref)
            resolution = self.index.resolve(embed.ref)
            if asset is None and resolution.ok and resolution.entry_id:
                asset = self.index.get(resolution.entry_id)
            if asset is None or asset.type != "asset":
                self.add(
                    "error",
                    "missing-asset",
                    f"no asset entry for {embed.kind}:{embed.ref}",
                    document,
                    line=embed.line,
                    field=embed.field,
                )

    def _hidden_names(self) -> None:
        for leak in self.campaign.hidden_name_leaks:
            document = self.index.docs.get(leak.source_id)
            target = self.index.name_of(leak.target_id)
            self.add(
                "warning",
                "shows-hidden-name",
                f"player-visible text {leak.raw} names GM-only {target!r}; players see the name "
                "as plain text (wrap it in {secret}...{/secret} if that is a spoiler)",
                document,
            )

    def _revealing_ids(self) -> None:
        for entry_id, document in self.index.docs.items():
            party_view = document.meta.get("party_view")
            if not isinstance(party_view, dict) or not party_view.get("name"):
                continue
            if party_view["name"] == document.name:
                continue
            true_slug = slugify(document.name)
            if true_slug and (true_slug in entry_id or entry_id in true_slug):
                self.add(
                    "warning",
                    "id-reveals-name",
                    f"id {entry_id!r} reveals the hidden name {document.name!r} in page URLs; "
                    f"rename the id (e.g. {slugify(str(party_view['name']))!r})",
                    document,
                    field="id",
                )

    def _parents(self) -> None:
        for entry_id, document in self.index.docs.items():
            value = document.meta.get("parent")
            if value is None:
                continue
            if self.index.link_id(value, source_id=entry_id) is None:
                self.add(
                    "error",
                    "bad-parent",
                    "parent must be exactly one [[link]] to another entry",
                    document,
                    field="parent",
                )
                continue
            seen = {entry_id}
            current = self.index.parent_of.get(entry_id)
            while current is not None:
                if current in seen:
                    self.add(
                        "error",
                        "parent-cycle",
                        "parent chain loops back to this entry",
                        document,
                        field="parent",
                    )
                    break
                seen.add(current)
                current = self.index.parent_of.get(current)


def validate(campaign: Campaign) -> list[Issue]:
    return Validator(campaign).run()
