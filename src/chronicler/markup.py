"""Inline markup: ``[[links]]``, embeds, and ``{secret}``/``{revealed}``/``{prep}`` spans.

Rules (enforced here, reported by the validator):

* Spans must open and close on the same line. Longer secrets use a ``## Heading {secret}``.
* Spans may not nest inside other spans (links and embeds inside spans are fine).
* Parsing fails *closed*: an unclosed ``{secret}`` hides the rest of its line.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Literal

SPAN_KINDS = ("secret", "revealed", "prep")
EMBED_PREFIXES = ("img", "audio", "map", "sheet")

_TOKEN_RE = re.compile(
    r"(?P<embed>!\[\[(?P<embed_body>[^\[\]\n]+)\]\])"
    r"|(?P<link>\[\[(?P<link_body>[^\[\]\n]+)\]\])"
    r"|(?P<revealed>\{revealed(?:\s+(?P<revealed_arg>[^{}\n]*))?\})"
    r"|(?P<open>\{(?P<open_kind>secret|prep)\})"
    r"|(?P<close>\{/(?P<close_kind>secret|revealed|prep)\})"
    r"|(?P<unknown>\{/?[a-z][a-z0-9_-]*(?:\s[^{}\n]*)?\})"
)


@dataclass(frozen=True)
class LinkTarget:
    """The parsed body of ``[[target#section|label]]``."""

    target: str
    section: str | None = None
    label: str | None = None

    @property
    def display(self) -> str:
        if self.label:
            return self.label
        if self.section and not self.target:
            return self.section
        return self.target


def parse_link_body(body: str) -> LinkTarget:
    label: str | None = None
    if "|" in body:
        body, label = body.split("|", 1)
        label = label.strip() or None
    section: str | None = None
    if "#" in body:
        body, section = body.split("#", 1)
        section = section.strip() or None
    return LinkTarget(body.strip(), section, label)


@dataclass
class Text:
    text: str


@dataclass
class Link:
    target: LinkTarget
    raw: str
    column: int = 0


@dataclass
class Embed:
    kind: str
    ref: str
    raw: str
    column: int = 0


@dataclass
class Span:
    kind: str
    arg: str | None = None
    children: list[Node] = field(default_factory=list)
    column: int = 0
    closed: bool = True


Node = Text | Link | Embed | Span


@dataclass(frozen=True)
class MarkupIssue:
    code: str
    message: str
    column: int
    level: Literal["error", "warning"] = "error"


def _embed_or_link(body: str, raw: str, column: int, *, bang: bool) -> Node:
    prefix, sep, rest = body.partition(":")
    if sep and prefix.strip().lower() in EMBED_PREFIXES:
        return Embed(prefix.strip().lower(), rest.strip(), raw, column)
    if bang:
        # ![[Name]] without a known prefix: treat as an image reference by name.
        return Embed("img", body.strip(), raw, column)
    return Link(parse_link_body(body), raw, column)


def parse_line(line: str) -> tuple[list[Node], list[MarkupIssue]]:
    """Parse one line of text into nodes, collecting markup problems."""
    nodes: list[Node] = []
    issues: list[MarkupIssue] = []
    open_span: Span | None = None
    position = 0

    def emit(node: Node) -> None:
        target = open_span.children if open_span is not None else nodes
        if isinstance(node, Text) and target and isinstance(target[-1], Text):
            target[-1].text += node.text
        else:
            target.append(node)

    for match in _TOKEN_RE.finditer(line):
        if match.start() > position:
            emit(Text(line[position : match.start()]))
        position = match.end()
        raw = match.group(0)
        column = match.start()
        if match["embed"]:
            emit(_embed_or_link(match["embed_body"], raw, column, bang=True))
        elif match["link"]:
            emit(_embed_or_link(match["link_body"], raw, column, bang=False))
        elif match["revealed"] or match["open"]:
            kind = "revealed" if match["revealed"] else match["open_kind"]
            if open_span is not None:
                issues.append(
                    MarkupIssue(
                        "nested-span",
                        f"{{{kind}}} cannot be placed inside {{{open_span.kind}}}",
                        column,
                    )
                )
                emit(Text(raw))
                continue
            arg = (
                match["revealed_arg"].strip()
                if match["revealed"] and match["revealed_arg"]
                else None
            )
            open_span = Span(kind, arg, column=column)
        elif match["close"]:
            kind = match["close_kind"]
            if open_span is None or open_span.kind != kind:
                issues.append(
                    MarkupIssue("unbalanced-span", f"{{/{kind}}} has no matching opener", column)
                )
                continue
            nodes.append(open_span)
            open_span = None
        else:
            issues.append(MarkupIssue("unknown-tag", f"unknown tag {raw}", column, level="warning"))
            emit(Text(raw))
    if position < len(line):
        emit(Text(line[position:]))
    if open_span is not None:
        issues.append(
            MarkupIssue(
                "unclosed-span",
                f"{{{open_span.kind}}} is not closed on the same line",
                open_span.column,
            )
        )
        open_span.closed = False
        nodes.append(open_span)
    return nodes, issues


# -- traversal and serialization -------------------------------------------------------


def iter_links(
    nodes: list[Node], spans: tuple[str, ...] = ()
) -> Iterator[tuple[Link, tuple[str, ...]]]:
    """Yield every link with the span kinds it sits inside."""
    for node in nodes:
        if isinstance(node, Link):
            yield node, spans
        elif isinstance(node, Span):
            yield from iter_links(node.children, (*spans, node.kind))


def iter_embeds(nodes: list[Node]) -> Iterator[Embed]:
    for node in nodes:
        if isinstance(node, Embed):
            yield node
        elif isinstance(node, Span):
            yield from iter_embeds(node.children)


def iter_spans(nodes: list[Node]) -> Iterator[Span]:
    for node in nodes:
        if isinstance(node, Span):
            yield node


def to_source(nodes: list[Node]) -> str:
    """Serialize nodes back to markup text."""
    out: list[str] = []
    for node in nodes:
        if isinstance(node, Text):
            out.append(node.text)
        elif isinstance(node, Link | Embed):
            out.append(node.raw)
        else:
            opener = (
                f"{{revealed {node.arg}}}"
                if node.kind == "revealed" and node.arg
                else f"{{{node.kind}}}"
            )
            closer = f"{{/{node.kind}}}" if node.closed else ""
            out.append(opener + to_source(node.children) + closer)
    return "".join(out)


def plain_text(nodes: list[Node]) -> str:
    """Readable text: links become their display text, embeds disappear, spans unwrap."""
    out: list[str] = []
    for node in nodes:
        if isinstance(node, Text):
            out.append(node.text)
        elif isinstance(node, Link):
            out.append(node.target.display)
        elif isinstance(node, Span):
            out.append(plain_text(node.children))
    return "".join(out)


LinkRewriter = Callable[[Link], Node]


def redact_nodes(nodes: list[Node], rewrite: LinkRewriter | None = None) -> list[Node]:
    """Player view: drop secret and prep spans, unwrap revealed spans.

    ``rewrite`` may replace links (e.g. turn a link to a GM-only entry into plain text).
    """
    out: list[Node] = []
    for node in nodes:
        if isinstance(node, Span):
            if node.kind == "revealed" and node.closed:
                out.extend(redact_nodes(node.children, rewrite))
            # secret, prep, and any unclosed span are removed entirely (fail closed)
        elif isinstance(node, Link) and rewrite is not None:
            out.append(rewrite(node))
        else:
            out.append(node)
    return out


def redact_text(text: str, rewrite: LinkRewriter | None = None) -> str:
    """Redact every line of a multi-line string for the player view."""
    return "\n".join(
        to_source(redact_nodes(parse_line(line)[0], rewrite)) for line in text.split("\n")
    )
