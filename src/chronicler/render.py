"""Render documents to HTML for one view.

Custom syntax is swapped for private-use placeholder characters before Markdown runs and
for real HTML afterwards, so Markdown never sees (or breaks) our tags. Raw HTML in entry
text is escaped.
"""

from __future__ import annotations

import datetime as _dt
import html
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from markdown_it import MarkdownIt

from chronicler.calendar import Calendar, CalendarError
from chronicler.document import Document, Section
from chronicler.index import CampaignIndex
from chronicler.markup import (
    Embed,
    Link,
    Node,
    Span,
    Text,
    parse_line,
    parse_text,
    plain_text,
    redact_nodes,
)

_PLACEHOLDER_RE = re.compile("\ue000(\\d+)\ue001")
_TASK_RE = re.compile(r"^(\s*[-*+]\s+)\[( |x|X)\]\s+")

SCORE_FIELDS = frozenset(
    {"disposition", "reputation", "ruler_approval", "standing", "feeling", "score", "default"}
)
PRIMARY_KEYS = ("name", "text", "to", "faction", "plot", "event", "location", "who")
DATE_KEYS = frozenset({"date", "start", "end", "deadline", "from", "to", "target_date"})
HIDDEN_DETAIL_FIELDS = frozenset(
    {
        "id",
        "type",
        "name",
        "aliases",
        "status",
        "tags",
        "parent",
        "secret",
        "secret_fields",
        "party_view",
    }
)


def label_for(key: str) -> str:
    return {
        "class": "Class",
        "hp_max": "HP max",
        "ac": "AC",
        "spell_dc": "Spell DC",
        "cr": "CR",
        "hp": "HP",
    }.get(key, key.replace("_", " ").capitalize())


def format_score(value: Any) -> str | None:
    try:
        number = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    sign = "+" if number > 0 else ""
    return f"{sign}{number}"


def entry_url(entry_id: str, section: str | None = None) -> str:
    return f"/entry/{entry_id}" + (f"#{section}" if section else "")


def place_url(owner_id: str, slug: str) -> str:
    return f"/place/{owner_id}/{slug}"


class Renderer:
    def __init__(self, index: CampaignIndex, calendar: Calendar, *, gm: bool) -> None:
        self.index = index
        self.calendar = calendar
        self.gm = gm
        self.md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
        self._prep_counter = 0

    # -- placeholders ---------------------------------------------------------------------

    def _swap(self, fragments: list[str], fragment: str) -> str:
        fragments.append(fragment)
        return f"\ue000{len(fragments) - 1}\ue001"

    @staticmethod
    def _restore(rendered: str, fragments: list[str]) -> str:
        return _PLACEHOLDER_RE.sub(lambda m: fragments[int(m.group(1))], rendered)

    def _nodes_to_md(self, nodes: list[Node], source_id: str, fragments: list[str]) -> str:
        if not self.gm:
            nodes = redact_nodes(nodes)  # defensive: player documents are already redacted
        out: list[str] = []
        for node in nodes:
            if isinstance(node, Text):
                out.append(node.text)
            elif isinstance(node, Link):
                out.append(self._swap(fragments, self._link_html(node, source_id)))
            elif isinstance(node, Embed):
                out.append(self._swap(fragments, self._embed_html(node)))
            elif isinstance(node, Span):
                out.append(self._swap(fragments, self._span_open(node)))
                out.append(self._nodes_to_md(node.children, source_id, fragments))
                out.append(self._swap(fragments, "</mark>" if node.kind == "prep" else "</span>"))
        return "".join(out)

    def _span_open(self, span: Span) -> str:
        group = f" · reveal: {span.attrs['reveal']}" if span.attrs.get("reveal") else ""
        if span.kind == "secret":
            title = html.escape(f"GM only{group}", quote=True)
            return f'<span class="secret" title="{title}">'
        if span.kind == "revealed":
            when = plain_text(parse_line(span.arg)[0]) if span.arg else ""
            title = html.escape((f"Revealed in {when}" if when else "Revealed") + group, quote=True)
            return f'<span class="revealed" title="{title}">'
        if span.continued:  # the rest of a multi-line prep note: same anchor as its start
            return '<mark class="prep">'
        self._prep_counter += 1
        return f'<mark class="prep" id="prep-{self._prep_counter}">'

    def _link_html(self, link: Link, source_id: str) -> str:
        label = html.escape(link.target.display)
        resolution = self.index.resolve_link(link.target, source_id=source_id)
        if resolution.ok and resolution.entry_id:
            if resolution.place_key:
                place = self.index.places[resolution.place_key]
                href = place_url(place.owner_id, place.slug)
            else:
                href = entry_url(resolution.entry_id, resolution.section_slug)
            status = self.index.docs[resolution.entry_id].meta.get("status", "canon")
            if self.gm and status == "retired":
                return f'<a class="link link-retired" href="{href}" title="Retired">{label}</a>'
            return f'<a class="link" href="{href}">{label}</a>'
        if not self.gm:
            return label
        hint = "not found"
        if resolution.status == "ambiguous":
            hint = "ambiguous: " + ", ".join(resolution.candidates)
        elif resolution.status == "missing-section":
            hint = "section not found"
        elif resolution.suggestions:
            hint = "did you mean: " + ", ".join(resolution.suggestions)
        return f'<span class="link-broken" title="{html.escape(hint, quote=True)}">{label}</span>'

    def _asset(self, ref: str) -> Document | None:
        document = self.index.get(ref)
        if document is not None and document.type == "asset":
            return document
        resolution = self.index.resolve(ref)
        if resolution.ok and resolution.entry_id:
            found = self.index.get(resolution.entry_id)
            if found is not None and found.type == "asset":
                return found
        return None

    def _embed_html(self, embed: Embed) -> str:
        asset = self._asset(embed.ref)
        if asset is None:
            if not self.gm:
                return ""
            return (
                f'<span class="link-broken" title="asset not found">{html.escape(embed.raw)}</span>'
            )
        src = f"/asset/{asset.id}"
        caption = html.escape(str(asset.meta.get("caption") or asset.name), quote=True)
        if embed.kind == "audio" or asset.meta.get("kind") == "audio":
            return (
                f'<audio class="embed-audio" controls preload="none" src="{src}" '
                f'title="{caption}"></audio>'
            )
        if embed.kind == "sheet":
            return f'<a class="link" href="{src}">{caption}</a>'
        return f'<img class="embed-img" src="{src}" alt="{caption}" loading="lazy">'

    # -- public API -----------------------------------------------------------------------

    def inline(self, text: str, source_id: str) -> str:
        """Render a short text value (frontmatter) as inline HTML; newlines become <br>."""
        parts: list[str] = []
        for nodes, _issues in parse_text(str(text)):
            fragments: list[str] = []
            rendered = self.md.renderInline(self._nodes_to_md(nodes, source_id, fragments))
            parts.append(self._restore(rendered, fragments))
        return "<br>".join(parts)

    def section_content(self, section: Section, source_id: str) -> str:
        fragments: list[str] = []
        lines: list[str] = []
        for i, line in enumerate(section.lines):
            if section.code[i]:
                lines.append(line)
                continue
            line = _TASK_RE.sub(lambda m: m.group(1) + ("☑ " if m.group(2) != " " else "☐ "), line)
            nodes, _ = parse_line(line)
            lines.append(self._nodes_to_md(nodes, source_id, fragments))
        return self._restore(self.md.render("\n".join(lines)), fragments)

    def body(self, document: Document) -> str:
        self._prep_counter = 0
        out: list[str] = []
        for section in document.sections:
            classes = ["entry-section"]
            if section.secret:
                classes.append("secret-section")
            if section.is_place:
                classes.append("place-section")
            out.append(f'<section class="{" ".join(classes)}" id="{section.slug or "top"}">')
            if section.level:
                level = min(6, section.level + 1)
                title = self.inline(section.title, document.id)
                badge = (
                    ' <span class="badge badge-secret">GM only</span>'
                    if self.gm and section.own_secret
                    else ""
                )
                kind = (
                    f' <span class="badge">{html.escape(section.kind)}</span>'
                    if section.kind
                    else ""
                )
                if section.is_place:
                    href = place_url(document.id, section.slug)
                    title = f'<a class="place-link" href="{href}">{title}</a>'
                out.append(f"<h{level}>{title}{kind}{badge}</h{level}>")
            out.append(self.section_content(section, document.id))
            out.append("</section>")
        return "\n".join(out)

    def place_body(self, document: Document, slug: str) -> str:
        """Render a sublocation section and its own subsections."""
        self._prep_counter = 0
        sections = document.sections
        start = next((i for i, s in enumerate(sections) if s.slug == slug), None)
        if start is None:
            return ""
        root = sections[start]
        out = [self.section_content(root, document.id)]
        for section in sections[start + 1 :]:
            if section.level <= root.level:
                break
            level = min(6, section.level)
            out.append(
                f'<h{level} id="{section.slug}">'
                f"{self.inline(section.title, document.id)}</h{level}>"
            )
            out.append(self.section_content(section, document.id))
        return "\n".join(out)

    def date(self, value: str) -> str | None:
        try:
            return self.calendar.format(self.calendar.parse(value))
        except CalendarError:
            try:
                return self.calendar.format_fuzzy(self.calendar.parse_fuzzy(value))
            except CalendarError:
                return None

    def value(self, value: Any, source_id: str, key: str = "") -> str:
        """Render any frontmatter value as HTML."""
        if value is None:
            return ""
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if key in SCORE_FIELDS and isinstance(value, int | float | Decimal | str):
            score = format_score(value)
            if score is not None:
                tone = (
                    "pos"
                    if not score.startswith("-") and score != "0.00"
                    else ("neg" if score.startswith("-") else "zero")
                )
                return f'<span class="score score-{tone}">{score}</span>'
        if isinstance(value, int | float | Decimal):
            return html.escape(str(value))
        if isinstance(value, _dt.date):
            return value.isoformat()
        if isinstance(value, str):
            if key in DATE_KEYS and "[[" not in value:
                formatted = self.date(value)
                if formatted:
                    return f'<span class="world-date">{html.escape(formatted)}</span>'
            return self.inline(value, source_id)
        if isinstance(value, list):
            if all(not isinstance(item, dict | list) for item in value):
                return ", ".join(self.value(item, source_id, key) for item in value)
            items = "".join(f"<li>{self.value(item, source_id, key)}</li>" for item in value)
            return f'<ul class="value-list">{items}</ul>'
        if isinstance(value, dict):
            parts = []
            primary = next((k for k in PRIMARY_KEYS if k in value), None)
            if primary is not None:
                lead = self.value(value[primary], source_id, primary)
                parts.append(f'<span class="kv-primary">{lead}</span>')
            for sub_key, item in value.items():
                if sub_key in ("secret", primary):
                    continue
                parts.append(
                    f'<span class="kv"><span class="k">'
                    f"{html.escape(label_for(str(sub_key)))}</span> "
                    f"{self.value(item, source_id, str(sub_key))}</span>"
                )
            classes = "kv-group secret" if value.get("secret") is True and self.gm else "kv-group"
            title = ' title="GM only"' if "secret" in classes else ""
            return f'<span class="{classes}"{title}>' + " · ".join(parts) + "</span>"
        return html.escape(str(value))
