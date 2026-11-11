"""Full-text search over one view's documents, returning every reference with context.

The Player view's search index is built from redacted documents, so secret text is
never searchable there.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chronicler.index import CampaignIndex, alias_names, walk_strings
from chronicler.markup import parse_text, plain_text

SNIPPET_RADIUS = 60
MAX_SNIPPETS = 5


@dataclass(frozen=True)
class Snippet:
    before: str
    match: str
    after: str
    section_slug: str | None = None


@dataclass
class Hit:
    entry_id: str
    name: str
    type: str
    score: int
    snippets: list[Snippet] = field(default_factory=list)
    total_matches: int = 0


@dataclass
class SearchResult:
    query: str
    hits: list[Hit]
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Line:
    text: str
    folded: str
    section_slug: str | None


def _plain(text: str) -> str:
    return " ".join(plain_text(nodes) for nodes, _issues in parse_text(text))


class SearchIndex:
    def __init__(self, index: CampaignIndex) -> None:
        self.index = index
        self._entries: dict[str, tuple[str, list[_Line]]] = {}
        for entry_id, document in index.docs.items():
            titles = " ".join([document.name, *alias_names(document.meta)])
            lines: list[_Line] = []
            for _path, value in walk_strings(
                {
                    k: v
                    for k, v in document.meta.items()
                    if k not in ("id", "type", "name", "aliases")
                },
                "",
            ):
                text = _plain(value)
                if text.strip():
                    lines.append(_Line(text, text.casefold(), None))
            for section in document.sections:
                if section.level:
                    title = _plain(section.title)
                    lines.append(_Line(title, title.casefold(), section.slug))
                for i, line in enumerate(section.lines):
                    text = line if section.code[i] else _plain(line)
                    if text.strip():
                        lines.append(_Line(text, text.casefold(), section.slug or None))
            self._entries[entry_id] = (titles.casefold(), lines)

    def search(
        self, query: str, *, entry_type: str | None = None, limit: int = 100
    ) -> SearchResult:
        terms = [t for t in query.casefold().split() if t]
        if not terms:
            return SearchResult(query, [])
        hits: list[Hit] = []
        for entry_id, (titles, lines) in self._entries.items():
            document = self.index.docs[entry_id]
            if entry_type and document.type != entry_type:
                continue
            haystack = titles + "\n" + "\n".join(line.folded for line in lines)
            if not all(term in haystack for term in terms):
                continue
            score = 0
            if all(term in titles for term in terms):
                score += 1000
                if query.casefold().strip() == document.name.casefold():
                    score += 1000
            hit = Hit(entry_id, document.name, document.type, score)
            for line in lines:
                position, term = _first_match(line.folded, terms)
                if position < 0:
                    continue
                hit.total_matches += sum(line.folded.count(t) for t in terms)
                if len(hit.snippets) < MAX_SNIPPETS:
                    hit.snippets.append(_snippet(line, position, len(term)))
            hit.score += hit.total_matches
            hits.append(hit)
        hits.sort(key=lambda h: (-h.score, h.name.casefold()))
        suggestions = () if hits else self.index.suggest_names(query)
        return SearchResult(query, hits[:limit], suggestions)


def _first_match(folded: str, terms: list[str]) -> tuple[int, str]:
    best = (-1, "")
    for term in terms:
        position = folded.find(term)
        if position >= 0 and (best[0] < 0 or position < best[0]):
            best = (position, term)
    return best


def _snippet(line: _Line, position: int, length: int) -> Snippet:
    start = max(0, position - SNIPPET_RADIUS)
    end = min(len(line.text), position + length + SNIPPET_RADIUS)
    before = ("…" if start > 0 else "") + line.text[start:position]
    after = line.text[position + length : end] + ("…" if end < len(line.text) else "")
    return Snippet(before, line.text[position : position + length], after, line.section_slug)
