"""Editor text operations: the toolbar buttons and the quick fixes for tag problems.

These live in Python (not in the browser) so they follow exactly the same tag rules as the
validator and the Player-view redaction, and so every rule is tested with pytest. The
browser sends the body text and the selection; it gets back one :class:`Edit` to apply.

Rules (agreed with the GM):

* **Wrap** (Secret / Prep): same-kind tags the selection touches merge into one
  continuous tag; a tag of another kind the selection partly covers is pulled fully
  inside; a tag of another kind *around* the selection is fine (the new tag nests).
  Hiding text that was already revealed asks first. Tags never cross a heading.
* **Unmark**: removes the innermost tag around the selection; text outside the selection
  keeps it (one tag becomes two).
* **Reveal**: turns the secret at the cursor into ``{revealed [[Session]]}`` (keeping its
  ``reveal=`` / ``about=`` settings), or wraps the selection.
* **Fixes**: close an unclosed tag at the end of its section or paragraph, or remove a
  stray tag.

Positions from the browser are UTF-16 code units (JavaScript strings); they are converted
here, so text with emoji or other astral characters stays correct.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from chronicler.document import FENCE_RE, HEADING_RE
from chronicler.markup import tag_events

Kind = Literal["secret", "revealed", "prep"]
_HEADING_SECRET_RE = re.compile(r"\s*\{secret(?:\s[^{}]*)?\}")


@dataclass(frozen=True)
class TextSpan:
    kind: str
    opener: str  # exact source of the opening tag
    args: str  # the opener's arguments (e.g. " reveal=vex about=[[Plot]]")
    open_start: int
    open_end: int
    close_start: int | None = None
    close_end: int | None = None

    @property
    def closed(self) -> bool:
        return self.close_start is not None

    @property
    def end(self) -> int:
        return self.close_end if self.close_end is not None else self.open_end

    def contains(self, start: int, end: int) -> bool:
        """The range sits inside this span's content."""
        return self.close_start is not None and self.open_end <= start and end <= self.close_start

    def overlaps(self, start: int, end: int) -> bool:
        return self.closed and self.open_start < end and self.end > start


@dataclass(frozen=True)
class Edit:
    """Replace ``text[start:end]`` with ``insert`` and select ``[sel_from, sel_to]``.

    ``insert`` is ``None`` when nothing changes (see ``notice``); ``confirm`` asks a
    question first (resend with ``confirmed=True``). Positions are UTF-16 code units.
    """

    start: int = 0
    end: int = 0
    insert: str | None = None
    sel_from: int = 0
    sel_to: int = 0
    notice: str = ""
    confirm: str = ""


# -- UTF-16 positions ---------------------------------------------------------------------------


def from_utf16(text: str, offset: int) -> int:
    """Python string index for a JavaScript (UTF-16) offset."""
    units = 0
    for index, char in enumerate(text):
        if units >= offset:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def to_utf16(text: str, index: int) -> int:
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text[:index])


def _edit(text: str, start: int, end: int, insert: str, sel_from: int, sel_to: int) -> Edit:
    """An edit in Python indexes, converted to UTF-16 for the browser.

    ``sel_from`` / ``sel_to`` are positions in the text *after* the edit.
    """
    after = text[:start] + insert + text[end:]
    return Edit(
        to_utf16(text, start),
        to_utf16(text, end),
        insert,
        to_utf16(after, sel_from),
        to_utf16(after, sel_to),
    )


def _notice(message: str) -> Edit:
    return Edit(notice=message)


# -- finding spans ------------------------------------------------------------------------------


def _lines(text: str) -> list[tuple[int, str]]:
    """(offset of the line start, line text) for every line."""
    result: list[tuple[int, str]] = []
    offset = 0
    for line in text.split("\n"):
        result.append((offset, line))
        offset += len(line) + 1
    return result


def find_spans(text: str) -> list[TextSpan]:
    """Every span in body text (closed and unclosed), by start position.

    Same stack rules as the parser: headings end every open span, code blocks are
    skipped, and a closer only closes the innermost span of its own kind.
    """
    spans: list[TextSpan] = []
    stack: list[TextSpan] = []
    in_fence = False
    for start, line in _lines(text):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if HEADING_RE.match(line):
            spans.extend(stack)
            stack = []
            continue
        for match in tag_events(line):
            if match["open"]:
                stack.append(
                    TextSpan(
                        match["open_kind"],
                        match.group(0),
                        match.group(0)[len(match["open_kind"]) + 1 : -1],
                        start + match.start(),
                        start + match.end(),
                    )
                )
            elif stack and stack[-1].kind == match["close_kind"]:
                span = stack.pop()
                spans.append(
                    TextSpan(
                        span.kind,
                        span.opener,
                        span.args,
                        span.open_start,
                        span.open_end,
                        start + match.start(),
                        start + match.end(),
                    )
                )
    spans.extend(stack)
    return sorted(spans, key=lambda s: s.open_start)


def _crosses_heading(text: str, start: int, end: int) -> bool:
    return any(HEADING_RE.match(line) for line in text[start:end].split("\n")[1:])


# -- toolbar --------------------------------------------------------------------------------------


def wrap(
    text: str,
    sel_start: int,
    sel_end: int,
    kind: Kind,
    *,
    opener: str | None = None,
    confirmed: bool = False,
) -> Edit:
    """Put the selection inside a tag (see the module docstring for the merge rules)."""
    a, b = from_utf16(text, sel_start), from_utf16(text, sel_end)
    spans = find_spans(text)
    if any(s.kind == kind and s.contains(a, b) for s in spans):
        return _notice(f"That text is already inside a {kind} tag.")
    if a == b:
        tag = opener or f"{{{kind}}}"
        return _edit(text, a, a, f"{tag}{{/{kind}}}", a + len(tag), a + len(tag))
    start, end = a, b
    merged: list[TextSpan] = []
    pulled: list[TextSpan] = []
    grew = True
    while grew:
        grew = False
        for span in spans:
            if not span.overlaps(start, end) or span in merged or span in pulled:
                continue
            if span.kind == kind:
                merged.append(span)
            elif start <= span.open_start and span.end <= end:
                continue  # already fully inside the selection: it nests
            elif span.contains(start, end):
                continue  # the selection sits inside it: the new tag nests
            else:
                pulled.append(span)
            new_start, new_end = min(start, span.open_start), max(end, span.end)
            if (new_start, new_end) != (start, end):
                start, end, grew = new_start, new_end, True
    if _crosses_heading(text, start, end):
        return _notice('A tag cannot cross a heading. Use "Section secret" for a whole section.')
    if kind == "secret" and not confirmed and any(s.kind == "revealed" for s in pulled):
        return Edit(
            confirm="The selection includes text the players already know (revealed). "
            "Hide it again?"
        )
    inner = text[start:end]
    cuts = sorted(
        [(s.open_start, s.open_end) for s in merged]
        + [(s.close_start, s.close_end) for s in merged if s.close_start is not None],
        reverse=True,
    )
    for cut_start, cut_end in cuts:
        if cut_end is not None:
            inner = inner[: cut_start - start] + inner[cut_end - start :]
    tag = opener or (merged[0].opener if merged else f"{{{kind}}}")
    insert = f"{tag}{inner}{{/{kind}}}"
    return _edit(text, start, end, insert, start + len(tag), start + len(tag) + len(inner))


def _innermost(text: str, a: int, b: int, kind: str | None = None) -> TextSpan | None:
    around = [
        s
        for s in find_spans(text)
        if (kind is None or s.kind == kind)
        and s.closed
        and (s.contains(a, b) or (a == b and s.open_start <= a <= s.end))
    ]
    return max(around, key=lambda s: s.open_start) if around else None


def unmark(text: str, sel_start: int, sel_end: int) -> Edit:
    """Remove the innermost tag around the selection (or cursor)."""
    a, b = from_utf16(text, sel_start), from_utf16(text, sel_end)
    span = _innermost(text, a, b)
    if span is None or span.close_start is None or span.close_end is None:
        return _notice("Put the cursor (or select text) inside a tag to unmark it.")
    closer = f"{{/{span.kind}}}"
    if a == b or (a <= span.open_end and b >= span.close_start):
        whole = text[span.open_end : span.close_start]
        return _edit(
            text,
            span.open_start,
            span.close_end,
            whole,
            span.open_start,
            span.open_start + len(whole),
        )
    left, middle, right = text[span.open_end : a], text[a:b], text[b : span.close_start]
    insert = (
        (f"{span.opener}{left}{closer}" if left else "")
        + middle
        + (f"{span.opener}{right}{closer}" if right else "")
    )
    middle_at = span.open_start + (len(span.opener) + len(left) + len(closer) if left else 0)
    return _edit(text, span.open_start, span.close_end, insert, middle_at, middle_at + len(middle))


def reveal(text: str, sel_start: int, sel_end: int, target: str) -> Edit:
    """Mark text as revealed in ``target`` (a ``[[Session]]`` link)."""
    a, b = from_utf16(text, sel_start), from_utf16(text, sel_end)
    span = _innermost(text, a, b, "secret")
    if span is None or span.close_start is None or span.close_end is None:
        return wrap(text, sel_start, sel_end, "revealed", opener=f"{{revealed {target}}}")
    opener = f"{{revealed {target}{span.args}}}"
    inner = text[span.open_end : span.close_start]
    insert = f"{opener}{inner}{{/revealed}}"
    return _edit(
        text,
        span.open_start,
        span.close_end,
        insert,
        span.open_start,
        span.open_start + len(insert),
    )


def heading_secret(text: str, cursor: int) -> Edit:
    """Toggle ``{secret}`` on the heading of the section the cursor is in."""
    position = from_utf16(text, cursor)
    heading: tuple[int, str] | None = None
    for start, line in _lines(text):
        if start > position:
            break
        if HEADING_RE.match(line):
            heading = (start, line)
    if heading is None:
        return _notice("Put the cursor inside a section (under a ## heading) first.")
    start, line = heading
    if _HEADING_SECRET_RE.search(line):
        updated, message = _HEADING_SECRET_RE.sub("", line, count=1), "Section is now public."
    else:
        updated, message = line.rstrip() + " {secret}", "Section is now secret."
    edit = _edit(text, start, start + len(line), updated, start, start + len(updated))
    return Edit(edit.start, edit.end, edit.insert, edit.sel_from, edit.sel_to, notice=message)


def insert_link(text: str, sel_start: int, sel_end: int, name: str) -> Edit:
    a, b = from_utf16(text, sel_start), from_utf16(text, sel_end)
    label = text[a:b]
    link = f"[[{name}|{label}]]" if label and label != name else f"[[{name}]]"
    return _edit(text, a, b, link, a + len(link), a + len(link))


def insert_embed(text: str, sel_start: int, sel_end: int, asset_id: str, kind: str) -> Edit:
    a, b = from_utf16(text, sel_start), from_utf16(text, sel_end)
    embed = f"[[audio:{asset_id}]]" if kind == "audio" else f"![[img:{asset_id}]]"
    return _edit(text, a, b, embed, a + len(embed), a + len(embed))


# -- quick fixes for tag problems -----------------------------------------------------------------


def _line_start(text: str, line: int) -> int | None:
    lines = _lines(text)
    return lines[line][0] if 0 <= line < len(lines) else None


def _tag_at(text: str, line: int, column: int) -> tuple[int, int, str, bool] | None:
    """(start, end, kind, is_opener) of the tag at a body line and column."""
    start = _line_start(text, line)
    if start is None:
        return None
    line_text = _lines(text)[line][1]
    for match in tag_events(line_text):
        if match.start() == column:
            kind = match["open_kind"] or match["close_kind"]
            return start + match.start(), start + match.end(), kind, bool(match["open"])
    return None


def close_unclosed(
    text: str, line: int, column: int, *, at: Literal["section", "paragraph"]
) -> Edit:
    """Add the missing closing tag at the end of the section (or paragraph)."""
    tag = _tag_at(text, line, column)
    if tag is None or not tag[3]:
        return _notice("That tag is no longer there; the text changed.")
    lines = _lines(text)
    last = line
    for index in range(line + 1, len(lines)):
        content = lines[index][1]
        if HEADING_RE.match(content):
            break
        if not content.strip():
            if at == "paragraph":
                break
            continue
        last = index
    start, content = lines[last]
    position = start + len(content.rstrip())
    closer = f"{{/{tag[2]}}}"
    return _edit(text, position, position, closer, position + len(closer), position + len(closer))


def remove_tag(text: str, line: int, column: int) -> Edit:
    tag = _tag_at(text, line, column)
    if tag is None:
        return _notice("That tag is no longer there; the text changed.")
    return _edit(text, tag[0], tag[1], "", tag[0], tag[0])
