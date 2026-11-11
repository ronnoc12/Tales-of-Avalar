"""In-memory index over a set of documents (one index per view: GM or Player).

Resolves names and aliases (with fuzzy suggestions), and computes everything that is
never typed by hand: backlinks, children, members, cast, who is where, reverse
relationships, sublocations, and unlinked mentions.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from rapidfuzz import fuzz, process

from chronicler.document import Document, Section, slugify
from chronicler.markup import (
    Embed,
    Link,
    LinkTarget,
    Span,
    Text,
    iter_embeds,
    iter_links,
    parse_line,
    parse_link_body,
    parse_text,
)

MIN_MENTION_LENGTH = 4


@dataclass(frozen=True)
class Place:
    """A sublocation written inline under a ``## Places`` heading."""

    key: str
    owner_id: str
    slug: str
    name: str
    kind: str | None
    section: Section


@dataclass(frozen=True)
class Resolution:
    status: Literal["ok", "missing", "ambiguous", "missing-section"]
    entry_id: str | None = None
    place_key: str | None = None
    section_slug: str | None = None
    candidates: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class Reference:
    source_id: str
    raw: str
    label: str
    resolution: Resolution
    field: str | None = None
    line: int | None = None
    section_slug: str | None = None
    spans: tuple[str, ...] = ()

    @property
    def target_id(self) -> str | None:
        return self.resolution.entry_id


@dataclass(frozen=True)
class EmbedRef:
    source_id: str
    kind: str
    ref: str
    field: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class Mention:
    source_id: str
    target_id: str
    text: str
    line: int
    snippet: str


@dataclass(frozen=True)
class Backref:
    """A computed relationship from another entry: who points here, and the item details."""

    source_id: str
    details: dict[str, Any] = field(default_factory=dict)


def walk_strings(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from walk_strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{i}]")


def _single_link(value: Any) -> LinkTarget | None:
    if not isinstance(value, str):
        return None
    nodes, _ = parse_line(value)
    links = [n for n in nodes if isinstance(n, Link)]
    return links[0].target if len(links) == 1 else None


def alias_names(meta: dict[str, Any]) -> list[str]:
    names: list[str] = []
    aliases = meta.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            if isinstance(alias, str):
                names.append(alias)
            elif isinstance(alias, dict) and alias.get("name"):
                names.append(str(alias["name"]))
    return names


class CampaignIndex:
    def __init__(self, documents: list[Document], *, suggest: bool = True) -> None:
        self.suggest = suggest
        self.docs: dict[str, Document] = {}
        self.duplicate_ids: dict[str, list[str]] = defaultdict(list)
        for document in documents:
            if not document.id:
                continue
            if document.id in self.docs:
                if not self.duplicate_ids[document.id]:
                    self.duplicate_ids[document.id].append(self.docs[document.id].rel_path)
                self.duplicate_ids[document.id].append(document.rel_path)
                continue
            self.docs[document.id] = document
        self.duplicate_ids = dict(self.duplicate_ids)

        self.places: dict[str, Place] = {}
        self.places_of: dict[str, list[Place]] = defaultdict(list)
        self._keys: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self._key_display: dict[str, str] = {}
        self._suggestable: set[str] = set()
        self._build_keys()

        self.references: list[Reference] = []
        self.embeds: list[EmbedRef] = []
        self.backlinks: dict[str, list[Reference]] = defaultdict(list)
        self._build_references()

        self.children: dict[str, list[str]] = defaultdict(list)
        self.parent_of: dict[str, str] = {}
        self.members: dict[str, list[Backref]] = defaultdict(list)
        self.cast: dict[str, list[Backref]] = defaultdict(list)
        self.event_cast: dict[str, list[Backref]] = defaultdict(list)
        self.here: dict[str, list[str]] = defaultdict(list)
        self.incoming: dict[str, list[Backref]] = defaultdict(list)
        self.presence: dict[str, list[Backref]] = defaultdict(list)
        self.decisions_for: dict[str, list[str]] = defaultdict(list)
        self._build_relations()
        self._mentions: list[Mention] | None = None

    # -- keys and resolution ------------------------------------------------------------

    def _add_key(self, text: str, target: tuple[str, str], *, suggest: bool = True) -> None:
        key = text.strip().casefold()
        if not key:
            return
        self._keys[key].add(target)
        self._key_display.setdefault(key, text.strip())
        if suggest:
            self._suggestable.add(key)

    def _build_keys(self) -> None:
        for entry_id, document in self.docs.items():
            target = ("entry", entry_id)
            self._add_key(document.name, target)
            self._add_key(entry_id, target, suggest=False)
            for alias in alias_names(document.meta):
                self._add_key(alias, target)
            for section in document.sections:
                if not section.is_place:
                    continue
                key = f"{entry_id}#{section.slug}"
                nodes, _ = parse_line(section.title)
                name = (
                    "".join(n.text if isinstance(n, Text) else "" for n in nodes) or section.title
                )
                place = Place(key, entry_id, section.slug, name.strip(), section.kind, section)
                self.places[key] = place
                self.places_of[entry_id].append(place)
                self._add_key(place.name, ("place", key))

    def shared_names(self) -> list[tuple[str, list[str]]]:
        """Names/aliases used by more than one entry, as (display name, entry ids)."""
        shared: list[tuple[str, list[str]]] = []
        for key, targets in self._keys.items():
            entries = sorted({value for kind, value in targets if kind == "entry"})
            if len(entries) > 1:
                shared.append((self._key_display[key], entries))
        return shared

    def suggest_names(self, text: str) -> tuple[str, ...]:
        if not self.suggest or not self._suggestable:
            return ()
        matches = process.extract(
            text.casefold(), sorted(self._suggestable), scorer=fuzz.WRatio, limit=3, score_cutoff=75
        )
        seen: list[str] = []
        for key, _score, _ in matches:
            label = self._key_display[key]
            if label not in seen:
                seen.append(label)
        return tuple(seen)

    def resolve_link(self, target: LinkTarget, *, source_id: str | None = None) -> Resolution:
        name = target.target
        if not name and source_id:
            candidates: set[tuple[str, str]] = {("entry", source_id)}
        else:
            candidates = self._keys.get(name.casefold(), set())
        if not candidates:
            return Resolution("missing", suggestions=self.suggest_names(name))
        if len(candidates) > 1:
            labels = tuple(sorted(self._label_for(c) for c in candidates))
            return Resolution("ambiguous", candidates=labels)
        kind, value = next(iter(candidates))
        if kind == "place":
            place = self.places[value]
            return Resolution("ok", place.owner_id, value, place.slug)
        if not target.section:
            return Resolution("ok", value)
        document = self.docs[value]
        section = self._find_section(document, target.section)
        if section is None:
            return Resolution("missing-section", value)
        place_key = f"{value}#{section.slug}" if section.is_place else None
        return Resolution("ok", value, place_key, section.slug)

    def resolve(self, text: str, *, source_id: str | None = None) -> Resolution:
        body = text.strip()
        if body.startswith("[[") and body.endswith("]]"):
            body = body[2:-2]
        return self.resolve_link(parse_link_body(body), source_id=source_id)

    def _label_for(self, target: tuple[str, str]) -> str:
        kind, value = target
        if kind == "place":
            place = self.places[value]
            return f"{self.docs[place.owner_id].name} › {place.name}"
        return f"{self.docs[value].name} ({value})"

    @staticmethod
    def _find_section(document: Document, wanted: str) -> Section | None:
        slug = slugify(wanted)
        for section in document.sections:
            if section.slug == slug or section.title.casefold() == wanted.casefold():
                return section
        return None

    def link_id(self, value: Any, *, source_id: str | None = None) -> str | None:
        """Entry id for a frontmatter value holding exactly one ``[[link]]``."""
        target = _single_link(value)
        if target is None:
            return None
        resolution = self.resolve_link(target, source_id=source_id)
        return resolution.entry_id if resolution.ok and resolution.place_key is None else None

    # -- references ---------------------------------------------------------------------

    def _build_references(self) -> None:
        for entry_id, document in self.docs.items():
            for path, text in walk_strings(document.meta, ""):
                for nodes, _issues in parse_text(text):
                    self._collect(entry_id, nodes, field=path)
            for section in document.sections:
                if section.level:
                    nodes, _ = parse_line(section.title)
                    self._collect(entry_id, nodes, line=section.line, section_slug=section.slug)
                for i, line in enumerate(section.lines):
                    if section.code[i]:
                        continue
                    nodes, _ = parse_line(line)
                    self._collect(
                        entry_id,
                        nodes,
                        line=section.content_line(i),
                        section_slug=section.slug,
                        section_secret=section.secret,
                    )

    def _collect(
        self,
        source_id: str,
        nodes: list[Any],
        *,
        field: str | None = None,
        line: int | None = None,
        section_slug: str | None = None,
        section_secret: bool = False,
    ) -> None:
        for link, spans in iter_links(nodes):
            resolution = self.resolve_link(link.target, source_id=source_id)
            all_spans = ("secret-section", *spans) if section_secret else spans
            reference = Reference(
                source_id,
                link.raw,
                link.target.display,
                resolution,
                field,
                line,
                section_slug,
                all_spans,
            )
            self.references.append(reference)
            if resolution.entry_id and resolution.entry_id != source_id:
                self.backlinks[resolution.entry_id].append(reference)
        embeds: list[Embed] = list(iter_embeds(nodes))
        for embed in embeds:
            self.embeds.append(EmbedRef(source_id, embed.kind, embed.ref, field, line))

    # -- computed relations -------------------------------------------------------------

    def _items(self, meta: dict[str, Any], key: str) -> list[dict[str, Any]]:
        value = meta.get(key)
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def _build_relations(self) -> None:
        for entry_id, document in self.docs.items():
            meta = document.meta
            parent = self.link_id(meta.get("parent"), source_id=entry_id)
            if parent and parent != entry_id:
                self.children[parent].append(entry_id)
                self.parent_of[entry_id] = parent
            for item in self._items(meta, "factions"):
                key = "faction" if "faction" in item else None
                target = self.link_id(item.get(key)) if key else None
                if target:
                    if document.type == "location":
                        self.presence[target].append(Backref(entry_id, item))
                    else:
                        self.members[target].append(Backref(entry_id, item))
            for item in self._items(meta, "presence"):
                target = self.link_id(item.get("location"))
                if target:
                    self.presence[target].append(Backref(entry_id, item))
            for item in self._items(meta, "plots"):
                target = self.link_id(item.get("plot"))
                if target:
                    self.cast[target].append(Backref(entry_id, item))
            for item in self._items(meta, "events"):
                target = self.link_id(item.get("event"))
                if target:
                    self.event_cast[target].append(Backref(entry_id, item))
            for key in ("relationships", "relations"):
                for item in self._items(meta, key):
                    target = self.link_id(item.get("to"))
                    if target and target != entry_id:
                        self.incoming[target].append(Backref(entry_id, item))
            if document.type in ("npc", "pc", "item", "scene"):
                location = self.link_id(meta.get("location"))
                if location:
                    self.here[location].append(entry_id)
            if document.type == "decision":
                plot = self.link_id(meta.get("plot"))
                if plot:
                    self.decisions_for[plot].append(entry_id)

    # -- mentions -----------------------------------------------------------------------

    def mentions(self) -> list[Mention]:
        """Unlinked mentions of entry names/aliases in body text (computed lazily)."""
        if self._mentions is not None:
            return self._mentions
        keys = sorted(
            (
                k
                for k, targets in self._keys.items()
                if len(k) >= MIN_MENTION_LENGTH
                and all(t[0] == "entry" for t in targets)
                and len(targets) == 1
            ),
            key=len,
            reverse=True,
        )
        found: list[Mention] = []
        if keys:
            pattern = re.compile(
                r"(?<![\w])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w])", re.IGNORECASE
            )
            for source_id, document in self.docs.items():
                linked = {r.target_id for r in self.references if r.source_id == source_id}
                for section in document.sections:
                    for i, line in enumerate(section.lines):
                        if section.code[i]:
                            continue
                        nodes, _ = parse_line(line)
                        text = "".join(
                            n.text if isinstance(n, Text) else " " * len(_node_source(n))
                            for n in _flatten(nodes)
                        )
                        for match in pattern.finditer(text):
                            ((_kind, target),) = self._keys[match.group(1).casefold()]
                            if target == source_id or target in linked:
                                continue
                            start = max(0, match.start() - 50)
                            snippet = text[start : match.end() + 50].strip()
                            found.append(
                                Mention(
                                    source_id,
                                    target,
                                    match.group(1),
                                    section.content_line(i),
                                    snippet,
                                )
                            )
        self._mentions = found
        return found

    # -- convenience --------------------------------------------------------------------

    def get(self, entry_id: str) -> Document | None:
        return self.docs.get(entry_id)

    def name_of(self, entry_id: str) -> str:
        document = self.docs.get(entry_id)
        return document.name if document else entry_id

    def of_type(self, entry_type: str) -> list[Document]:
        return sorted(
            (d for d in self.docs.values() if d.type == entry_type), key=lambda d: d.name.casefold()
        )

    def tree(self, entry_type: str) -> list[tuple[Document, int]]:
        """Entries of one type as (document, depth) in tree order (parents before children)."""
        entries = {d.id: d for d in self.of_type(entry_type)}
        roots = [d for d in entries.values() if self.parent_of.get(d.id) not in entries]
        result: list[tuple[Document, int]] = []
        seen: set[str] = set()

        def visit(document: Document, depth: int) -> None:
            if document.id in seen:
                return
            seen.add(document.id)
            result.append((document, depth))
            kids = sorted(
                (entries[c] for c in self.children.get(document.id, []) if c in entries),
                key=lambda d: d.name.casefold(),
            )
            for kid in kids:
                visit(kid, depth + 1)

        for root in sorted(roots, key=lambda d: d.name.casefold()):
            visit(root, 0)
        for document in entries.values():  # entries caught in a parent cycle
            visit(document, 0)
        return result

    def type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for document in self.docs.values():
            counts[document.type] += 1
        return dict(counts)


def _flatten(nodes: list[Any]) -> Iterator[Any]:
    for node in nodes:
        if isinstance(node, Span):
            yield from _flatten(node.children)
        else:
            yield node


def _node_source(node: Any) -> str:
    raw = getattr(node, "raw", "")
    return str(raw)
