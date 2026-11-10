"""Redaction: build the Player view of documents.

Rules, per field: a ``party_view`` value wins; otherwise the field is hidden if it is
GM-only (by type, ``secret_fields``, or ``secret: true`` on an item); otherwise the real
value is shown with inline ``{secret}`` and ``{prep}`` spans stripped and ``{revealed}``
spans unwrapped. Whole entries are hidden unless they are canon and not secret.

Links in visible text are rewritten using the GM index: a link to an entry with a
``party_view`` name points at that name, and a link to anything players cannot see
becomes plain text (and is recorded, so the validator can warn that its name shows).

Everything here fails closed: when in doubt, content is removed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chronicler.document import Document, Section
from chronicler.markup import Link, LinkRewriter, LinkTarget, Node, Text, redact_text
from chronicler.schema import (
    GM_ONLY_COMMON,
    GM_ONLY_FIELDS,
    GM_ONLY_NESTED,
    is_secret_entry,
)

if TYPE_CHECKING:
    from chronicler.index import CampaignIndex


class _Drop:
    """Marker for values removed by redaction."""


DROP = _Drop()


def is_player_visible(meta: dict[str, Any]) -> bool:
    return meta.get("status", "canon") == "canon" and not is_secret_entry(meta)


@dataclass(frozen=True)
class HiddenNameLeak:
    """Player-visible text that links to something players cannot see."""

    source_id: str
    raw: str
    target_id: str


@dataclass
class LinkPolicy:
    """Rewrites links in one document's visible text for the Player view."""

    gm_index: CampaignIndex
    source_id: str
    leaks: list[HiddenNameLeak] = field(default_factory=list)

    def __call__(self, link: Link) -> Node:
        resolution = self.gm_index.resolve_link(link.target, source_id=self.source_id)
        if not resolution.ok or resolution.entry_id is None:
            return link
        target = self.gm_index.docs[resolution.entry_id]
        hidden = not is_player_visible(target.meta)
        if not hidden and resolution.place_key:
            hidden = self.gm_index.places[resolution.place_key].section.secret
        if hidden:
            if resolution.entry_id != self.source_id:
                self.leaks.append(HiddenNameLeak(self.source_id, link.raw, resolution.entry_id))
            return Text(link.target.display)
        party_view = target.meta.get("party_view")
        party_name = party_view.get("name") if isinstance(party_view, dict) else None
        if party_name and not link.target.label and resolution.entry_id != self.source_id:
            name = str(party_name)
            section = f"#{link.target.section}" if link.target.section else ""
            return Link(LinkTarget(name, link.target.section), f"[[{name}{section}]]", link.column)
        return link


def redact_value(value: Any, rewrite: LinkRewriter | None = None) -> Any:
    """Redact one frontmatter value; returns :data:`DROP` if nothing visible remains."""
    if isinstance(value, str):
        cleaned = redact_text(value, rewrite)
        return DROP if value.strip() and not cleaned.strip() else cleaned
    if isinstance(value, dict):
        if value.get("secret") is True:
            return DROP
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key == "secret" or key in GM_ONLY_NESTED:
                continue
            cleaned = redact_value(item, rewrite)
            if cleaned is not DROP:
                result[str(key)] = cleaned
        return result if result or not value else DROP
    if isinstance(value, list):
        items = [redact_value(item, rewrite) for item in value]
        kept = [item for item in items if item is not DROP]
        return kept if kept or not value else DROP
    return value


def gm_only_fields(meta: dict[str, Any]) -> frozenset[str]:
    entry_type = str(meta.get("type", ""))
    extra = meta.get("secret_fields") or []
    named = frozenset(str(f) for f in extra) if isinstance(extra, list) else frozenset()
    return GM_ONLY_COMMON | GM_ONLY_FIELDS.get(entry_type, frozenset()) | named


def redact_meta(meta: dict[str, Any], rewrite: LinkRewriter | None = None) -> dict[str, Any]:
    party_view = meta.get("party_view")
    overrides: dict[str, Any] = party_view if isinstance(party_view, dict) else {}
    hidden = gm_only_fields(meta)
    result: dict[str, Any] = {}
    for key, value in meta.items():
        if key in ("id", "type"):
            result[key] = value
            continue
        if key in overrides or key in hidden:
            continue
        cleaned = redact_value(value, rewrite)
        if cleaned is not DROP:
            result[key] = cleaned
    for key, value in overrides.items():
        if key in ("id", "type"):
            continue
        cleaned = redact_value(value, rewrite)
        if cleaned is not DROP:
            result[str(key)] = cleaned
    result.setdefault("name", result.get("id", ""))
    return result


def redact_sections(sections: list[Section], rewrite: LinkRewriter | None = None) -> list[Section]:
    result: list[Section] = []
    for section in sections:
        if section.secret:
            continue
        result.append(
            Section(
                level=section.level,
                title=redact_text(section.title, rewrite),
                slug=section.slug,
                attrs=tuple(a for a in section.attrs if a != "secret"),
                line=section.line,
                lines=[redact_text(line, rewrite) for line in section.lines],
                code=list(section.code),
                secret=False,
                parent_slug=section.parent_slug,
                is_place=section.is_place,
            )
        )
    return result


def redact_document(document: Document, rewrite: LinkRewriter | None = None) -> Document | None:
    """The Player version of a document, or ``None`` if players cannot see it at all."""
    if not is_player_visible(document.meta):
        return None
    return document.copy_with(
        meta=redact_meta(document.meta, rewrite),
        sections=redact_sections(document.sections, rewrite),
    )


@dataclass
class PlayerDocuments:
    documents: list[Document]
    leaks: list[HiddenNameLeak]


def player_documents(
    documents: list[Document], gm_index: CampaignIndex | None = None
) -> PlayerDocuments:
    """Redact every document. With ``gm_index``, links are rewritten and leaks recorded."""
    result: list[Document] = []
    leaks: list[HiddenNameLeak] = []
    for document in documents:
        policy = LinkPolicy(gm_index, document.id) if gm_index is not None else None
        redacted = redact_document(document, policy)
        if redacted is not None:
            result.append(redacted)
            if policy is not None:
                leaks.extend(policy.leaks)
    return PlayerDocuments(result, leaks)


@dataclass(frozen=True)
class Mismatch:
    """A field where the party believes something different from the truth."""

    entry_id: str
    entry_name: str
    field: str
    truth: Any
    party_believes: Any


def find_mismatches(documents: list[Document]) -> list[Mismatch]:
    result: list[Mismatch] = []
    for document in documents:
        party_view = document.meta.get("party_view")
        if not isinstance(party_view, dict):
            continue
        for key, believed in party_view.items():
            truth = document.meta.get(key)
            if truth != believed:
                result.append(Mismatch(document.id, document.name, str(key), truth, believed))
    return result
