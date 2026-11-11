"""Inline markup: ``[[links]]``, embeds, and ``{secret}``/``{revealed}``/``{prep}`` spans.

Rules (enforced here, reported by the validator):

* A span is one continuous tag. It may cross lines, but not headings: a span still open
  at the end of its section is *unclosed*.
* A span may sit fully inside another span. Partly overlapping spans are an error.
* Player view shows text only if every span around it is visible.
* Parsing fails *closed*: an unclosed span hides everything to the end of its section,
  and an unclosed ``{revealed}`` is treated as a ``{secret}``.

Multi-line spans are *balanced* before anything else sees them (:func:`balance`): the
span is closed at the end of each line and reopened on the next, with a private-use
marker (:data:`CONTINUE`) on the reopened pieces. Everything downstream (rendering,
redaction, search, the index) can then keep working one line at a time. The marker can
never come from a file: the document parser strips private-use characters.

Openers take optional attributes: ``{secret reveal=vex-letter about=[[Velkyn Plot]]}``
groups secrets for a prepared reveal; ``{revealed [[Session 7#Notes]] reveal=vex-letter}``
records where the reveal happened.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

SPAN_KINDS = ("secret", "revealed", "prep")
HIDING_KINDS = frozenset({"secret", "prep"})
EMBED_PREFIXES = ("img", "audio", "map", "sheet")
CONTINUE = ""  # marks a span piece that continues from the previous line
SPAN_ATTRS: dict[str, frozenset[str]] = {
    "secret": frozenset({"reveal", "about"}),
    "revealed": frozenset({"reveal", "about"}),
    "prep": frozenset(),
}
GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_ARGS = r"(?:[^{}\[\]\n]|\[\[[^\[\]\n]*\]\])*"
_TOKEN_RE = re.compile(
    r"(?P<embed>!\[\[(?P<embed_body>[^\[\]\n]+)\]\])"
    r"|(?P<link>\[\[(?P<link_body>[^\[\]\n]+)\]\])"
    rf"|(?P<open>(?P<cont>{CONTINUE})?\{{(?P<open_kind>secret|revealed|prep)"
    rf"(?:\s+(?P<open_args>{_ARGS}))?\}})"
    r"|(?P<close>\{/(?P<close_kind>secret|revealed|prep)\})"
    r"|(?P<unknown>\{/?[a-z][a-z0-9_-]*(?:\s[^{}\n]*)?\})"
)
ATTR_RE = re.compile(r'(?P<key>[a-z][a-z0-9_]*)=(?P<value>\[\[[^\[\]\n]*\]\]|"[^"\n]*"|[^\s"]+)')
# Markdown block markers that must stay at the start of a line: quotes, list items, tasks.
BLOCK_PREFIX_RE = re.compile(
    r"^[ \t]*(?:>[ \t]?)*[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?)?"
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


def parse_args(raw: str | None) -> tuple[str | None, dict[str, str]]:
    """Split opener arguments into free text (``{revealed}``'s session) and ``key=value``."""
    if not raw:
        return None, {}
    attrs: dict[str, str] = {}
    for match in ATTR_RE.finditer(raw):
        value = match["value"]
        attrs[match["key"]] = value[1:-1] if value.startswith('"') else value
    free = ATTR_RE.sub("", raw).strip()
    return free or None, attrs


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
    attrs: dict[str, str] = field(default_factory=dict)
    continued: bool = False
    opener: str = ""  # exact source of the opening tag (without the continue marker)

    def opener_source(self) -> str:
        if self.opener:
            return self.opener
        parts = [self.kind]
        if self.arg:
            parts.append(self.arg)
        parts += [f"{k}={v}" for k, v in self.attrs.items()]
        return "{" + " ".join(parts) + "}"


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


def check_attrs(kind: str, attrs: dict[str, str], column: int) -> list[MarkupIssue]:
    """Syntax checks for opener attributes (link targets are checked by the validator)."""
    issues: list[MarkupIssue] = []
    for key, value in attrs.items():
        if key not in SPAN_ATTRS.get(kind, frozenset()):
            issues.append(
                MarkupIssue("unknown-attr", f"{{{kind}}} has no {key!r} setting", column, "warning")
            )
        elif key == "reveal" and not GROUP_RE.match(value):
            issues.append(
                MarkupIssue(
                    "bad-attr",
                    f"reveal group {value!r} must be lowercase-with-dashes",
                    column,
                )
            )
        elif key == "about" and not (value.startswith("[[") and value.endswith("]]")):
            issues.append(MarkupIssue("bad-attr", "about= must be a [[link]]", column))
    return issues


def parse_line(line: str) -> tuple[list[Node], list[MarkupIssue]]:
    """Parse one line of text into nodes, collecting markup problems."""
    nodes: list[Node] = []
    issues: list[MarkupIssue] = []
    stack: list[Span] = []
    position = 0

    def emit(node: Node) -> None:
        target = stack[-1].children if stack else nodes
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
        elif match["open"]:
            kind = match["open_kind"]
            arg, attrs = parse_args(match["open_args"])
            issues.extend(check_attrs(kind, attrs, column))
            continued = bool(match["cont"])
            span = Span(
                kind,
                arg,
                column=column,
                attrs=attrs,
                continued=continued,
                opener=raw[len(CONTINUE) :] if continued else raw,
            )
            emit(span)
            stack.append(span)
        elif match["close"]:
            kind = match["close_kind"]
            if stack and stack[-1].kind == kind:
                stack.pop()
            elif any(span.kind == kind for span in stack):
                # Partly overlapping spans. Ignore the closer: the span stays open (fail closed).
                issues.append(
                    MarkupIssue(
                        "overlapping-spans",
                        f"{{/{kind}}} closes across {{{stack[-1].kind}}}; spans may sit inside "
                        "each other but not partly overlap",
                        column,
                    )
                )
            else:
                issues.append(
                    MarkupIssue("unbalanced-span", f"{{/{kind}}} has no matching opener", column)
                )
        else:
            issues.append(MarkupIssue("unknown-tag", f"unknown tag {raw}", column, level="warning"))
            emit(Text(raw))
    if position < len(line):
        emit(Text(line[position:]))
    for span in stack:
        span.closed = False
        issues.append(MarkupIssue("unclosed-span", f"{{{span.kind}}} is not closed", span.column))
    return nodes, issues


# -- multi-line spans ------------------------------------------------------------------


@dataclass
class Balanced:
    """Lines with every span closed on its own line (see the module docstring)."""

    lines: list[str]
    hidden: list[bool]  # code lines inside a hiding span: players never see them
    issues: list[tuple[int, MarkupIssue]]  # (line index, issue)


def tag_events(line: str) -> Iterator[re.Match[str]]:
    """Span openers and closers in one line, as regex matches (groups: ``open``,
    ``open_kind``, ``open_args``, ``close``, ``close_kind``). Links are skipped over, so
    braces inside ``[[...]]`` never count."""
    for match in _TOKEN_RE.finditer(line):
        if match["open"] or match["close"]:
            yield match


def balance(lines: Sequence[str], code: Sequence[bool] | None = None) -> Balanced:
    """Close and reopen spans at line breaks; report spans that never close.

    Stack rules match :func:`parse_line`: a closer only closes the innermost span of its
    kind; any other closer is left for ``parse_line`` to report.
    """
    is_code = list(code) if code is not None else [False] * len(lines)
    # Pass 1: which openers never close before the end of this block?
    pending: list[tuple[str, int, int]] = []
    for i, line in enumerate(lines):
        if is_code[i]:
            continue
        for match in tag_events(line):
            if match["open"]:
                pending.append((match["open_kind"], i, match.start()))
            elif pending and pending[-1][0] == match["close_kind"]:
                pending.pop()
    unclosed = {(i, column) for _kind, i, column in pending}

    # Pass 2: rebuild each line with its spans reopened and closed.
    out: list[str] = []
    hidden: list[bool] = []
    issues: list[tuple[int, MarkupIssue]] = []
    open_spans: list[tuple[str, str]] = []  # (kind, opener source)
    for i, line in enumerate(lines):
        if is_code[i] or not line.strip():
            out.append(line)
            hidden.append(is_code[i] and any(kind in HIDING_KINDS for kind, _ in open_spans))
            continue
        reopen = "".join(CONTINUE + source for _kind, source in open_spans)
        rebuilt: list[str] = []
        position = 0
        for match in tag_events(line):
            if match["open"]:
                kind, source = match["open_kind"], match.group(0).removeprefix(CONTINUE)
                if (i, match.start()) in unclosed:
                    issues.append(
                        (
                            i,
                            MarkupIssue(
                                "unclosed-span",
                                f"{{{kind}}} is never closed: it hides the rest of the section",
                                match.start(),
                            ),
                        )
                    )
                    if kind == "revealed":  # an unfinished reveal must not show anything
                        kind, source = "secret", "{secret}"
                        rebuilt.append(line[position : match.start()] + source)
                        position = match.end()
                open_spans.append((kind, source))
            elif open_spans and open_spans[-1][0] == match["close_kind"]:
                open_spans.pop()
        rebuilt.append(line[position:])
        text = "".join(rebuilt)
        prefix = BLOCK_PREFIX_RE.match(text)
        cut = prefix.end() if prefix else 0
        text = text[:cut] + reopen + text[cut:]
        if open_spans:
            stripped = text.rstrip()
            closers = "".join(f"{{/{kind}}}" for kind, _ in reversed(open_spans))
            text = stripped + closers + text[len(stripped) :]
        out.append(text)
        hidden.append(False)
    return Balanced(out, hidden, issues)


def parse_text(text: str) -> list[tuple[list[Node], list[MarkupIssue]]]:
    """Parse multi-line text (a frontmatter value): balance, then parse line by line."""
    balanced = balance(text.split("\n"))
    extra: dict[int, list[MarkupIssue]] = defaultdict(list)
    for i, issue in balanced.issues:
        extra[i].append(issue)
    result: list[tuple[list[Node], list[MarkupIssue]]] = []
    for i, line in enumerate(balanced.lines):
        nodes, issues = parse_line(line)
        result.append((nodes, extra[i] + issues))
    return result


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
    """Every span, outer before inner, in reading order (the renderer's order)."""
    for node in nodes:
        if isinstance(node, Span):
            yield node
            yield from iter_spans(node.children)


def to_source(nodes: list[Node]) -> str:
    """Serialize nodes back to markup text."""
    out: list[str] = []
    for node in nodes:
        if isinstance(node, Text):
            out.append(node.text)
        elif isinstance(node, Link | Embed):
            out.append(node.raw)
        else:
            marker = CONTINUE if node.continued else ""
            closer = f"{{/{node.kind}}}" if node.closed else ""
            out.append(marker + node.opener_source() + to_source(node.children) + closer)
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

    Spans may nest, so a revealed span's children are redacted too (a secret inside a
    reveal stays secret). ``rewrite`` may replace links (e.g. turn a link to a GM-only
    entry into plain text).
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


def redact_line(line: str, rewrite: LinkRewriter | None = None) -> str:
    return to_source(redact_nodes(parse_line(line)[0], rewrite))


def redact_text(text: str, rewrite: LinkRewriter | None = None) -> str:
    """Redact multi-line text for the player view (spans may cross lines)."""
    return "\n".join(redact_line(line, rewrite) for line in balance(text.split("\n")).lines)


def emptied_by_redaction(original: str, redacted: str) -> bool:
    """True when redaction left nothing but a list/quote marker (or nothing) on a line.

    Such lines are dropped from the Player view, so a hidden list item does not leave an
    empty bullet behind (or hint that something was there).
    """
    if not original.strip() or original == redacted:
        return False
    prefix = BLOCK_PREFIX_RE.match(redacted)
    return not redacted[prefix.end() if prefix else 0 :].strip()
