"""Parse one entry file: YAML frontmatter plus a Markdown body split into sections."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

# Private-use characters reserved for the renderer's placeholders.
_RESERVED_RE = re.compile("[\ue000-\ue00f]")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.*?)\s*#*\s*$")
_ATTR_RE = re.compile(r"\s*\{(?P<attr>[a-z][a-z0-9_-]*)\}\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_LINK_IN_TITLE_RE = re.compile(r"!?\[\[([^\]|#]*)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")

PLACES_TITLE = "places"


@dataclass(frozen=True)
class DocumentIssue:
    code: str
    message: str
    line: int | None = None
    level: str = "error"


@dataclass
class Section:
    """A heading and the lines under it (up to the next heading of any level)."""

    level: int
    title: str
    slug: str
    attrs: tuple[str, ...]
    line: int
    lines: list[str]
    code: list[bool]
    secret: bool = False
    parent_slug: str | None = None
    is_place: bool = False

    @property
    def own_secret(self) -> bool:
        return "secret" in self.attrs

    @property
    def kind(self) -> str | None:
        """A non-``secret`` heading attribute, e.g. ``### The Gilded Anchor {inn}``."""
        for attr in self.attrs:
            if attr != "secret":
                return attr
        return None

    def content_line(self, index: int) -> int:
        """1-based file line number of ``lines[index]``."""
        return self.line + 1 + index if self.level else self.line + index


@dataclass
class Document:
    rel_path: str
    meta: dict[str, Any]
    sections: list[Section]
    issues: list[DocumentIssue] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.meta.get("id") or "")

    @property
    def type(self) -> str:
        return str(self.meta.get("type") or "")

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or self.id)

    def section(self, slug: str) -> Section | None:
        for section in self.sections:
            if section.slug == slug:
                return section
        return None

    def copy_with(self, *, meta: dict[str, Any], sections: list[Section]) -> Document:
        return replace(self, meta=meta, sections=sections, issues=[])


def slugify(text: str) -> str:
    """URL/anchor-safe slug: ``"Lord Burgenroot's Hall"`` -> ``"lord-burgenroots-hall"``."""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    normalized = _LINK_IN_TITLE_RE.sub(lambda m: m.group(2) or m.group(1), normalized)
    normalized = re.sub(r"['’]", "", normalized.lower())
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "section"


def _load_yaml(text: str) -> Any:
    yaml = YAML(typ="safe", pure=True)
    return yaml.load(StringIO(text))


def split_frontmatter(text: str) -> tuple[str | None, str, int]:
    """Return (frontmatter_text, body_text, body_first_line_number)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text, 1
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :]), index + 2
    return None, text, 1


def _split_sections(body: str, first_line: int) -> list[Section]:
    sections: list[Section] = [Section(0, "", "", (), first_line, [], [])]
    used_slugs: dict[str, int] = {}
    in_fence = False
    for offset, raw_line in enumerate(body.split("\n")):
        line_no = first_line + offset
        fence = bool(_FENCE_RE.match(raw_line))
        heading = None if (in_fence or fence) else _HEADING_RE.match(raw_line)
        if heading:
            title = heading["title"]
            attrs: list[str] = []
            while True:
                attr_match = _ATTR_RE.search(title)
                if not attr_match:
                    break
                attrs.insert(0, attr_match["attr"])
                title = title[: attr_match.start()]
            title = title.strip()
            slug = slugify(title)
            count = used_slugs.get(slug, 0) + 1
            used_slugs[slug] = count
            if count > 1:
                slug = f"{slug}-{count}"
            sections.append(
                Section(len(heading["hashes"]), title, slug, tuple(attrs), line_no, [], [])
            )
            continue
        current = sections[-1]
        current.lines.append(raw_line)
        current.code.append(in_fence or fence)
        if fence:
            in_fence = not in_fence
    _link_sections(sections)
    preamble_empty = not any(line.strip() for line in sections[0].lines)
    if preamble_empty and len(sections) > 1:
        sections = sections[1:]
    return sections


def _link_sections(sections: list[Section]) -> None:
    """Fill in secret inheritance, parent slugs, and which sections are sublocations."""
    stack: list[Section] = []
    for section in sections:
        if section.level == 0:
            continue
        while stack and stack[-1].level >= section.level:
            stack.pop()
        parent = stack[-1] if stack else None
        section.parent_slug = parent.slug if parent else None
        section.secret = section.own_secret or bool(parent and parent.secret)
        section.is_place = bool(
            parent
            and parent.title.strip().casefold() == PLACES_TITLE
            and section.level == parent.level + 1
        )
        stack.append(section)


def parse_document(text: str, rel_path: str) -> Document:
    """Parse file text. Problems are recorded on the document, never raised."""
    text = _RESERVED_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    issues: list[DocumentIssue] = []
    front, body, body_line = split_frontmatter(text)
    meta: dict[str, Any] = {}
    if front is None:
        issues.append(DocumentIssue("no-frontmatter", "file has no '---' frontmatter block", 1))
    else:
        try:
            loaded = _load_yaml(front)
        except YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            line = mark.line + 2 if mark is not None else 1
            issues.append(DocumentIssue("yaml-error", f"invalid YAML: {exc}", line))
            loaded = None
        if loaded is None:
            loaded = {}
        if isinstance(loaded, dict):
            meta = {str(k): v for k, v in loaded.items()}
        else:
            issues.append(DocumentIssue("yaml-error", "frontmatter must be a mapping", 2))
    return Document(rel_path, meta, _split_sections(body, body_line), issues)
