"""Toolbar and quick-fix text operations (the rules the GM agreed on)."""

from __future__ import annotations

import pytest

from chronicler import edit_text as et
from chronicler.document import parse_document
from chronicler.edit_text import Edit


def u16(text: str, index: int) -> int:
    return et.to_utf16(text, index)


def sel(text: str, part: str, occurrence: int = 1) -> tuple[int, int]:
    """UTF-16 selection of ``part`` (its nth occurrence) in ``text``."""
    index = -1
    for _ in range(occurrence):
        index = text.index(part, index + 1)
    return u16(text, index), u16(text, index + len(part))


def apply(text: str, edit: Edit) -> str:
    assert edit.insert is not None, edit.notice or edit.confirm
    start, end = et.from_utf16(text, edit.start), et.from_utf16(text, edit.end)
    return text[:start] + edit.insert + text[end:]


def selected(text: str, edit: Edit) -> str:
    after = apply(text, edit)
    return after[et.from_utf16(after, edit.sel_from) : et.from_utf16(after, edit.sel_to)]


def clean(body: str) -> bool:
    doc = parse_document("---\nid: a\n---\n" + body, "a.md")
    return not any(s.markup_issues for s in doc.sections)


# -- wrap ---------------------------------------------------------------------------------------


def test_lazy_fox_expands_to_one_continuous_tag() -> None:
    text = "The lazy {secret}fox{/secret} jumps over the log"
    edit = et.wrap(text, *sel(text, text), "secret")
    assert apply(text, edit) == "{secret}The lazy fox jumps over the log{/secret}"
    assert selected(text, edit) == "The lazy fox jumps over the log"


def test_wrap_plain_selection_and_empty_cursor() -> None:
    text = "one two three"
    assert apply(text, et.wrap(text, *sel(text, "two"), "prep")) == "one {prep}two{/prep} three"
    edit = et.wrap(text, 3, 3, "secret")
    assert apply(text, edit) == "one{secret}{/secret} two three"
    assert edit.sel_from == edit.sel_to == 3 + len("{secret}")


def test_touching_secrets_merge_and_keep_the_first_ones_settings() -> None:
    text = "a {secret reveal=vex}b{/secret} c {secret}d{/secret} e"
    edit = et.wrap(text, *sel(text, "b{/secret} c {secret}d"), "secret")
    assert apply(text, edit) == "a {secret reveal=vex}b c d{/secret} e"


def test_partly_covered_tag_of_another_kind_is_pulled_inside() -> None:
    text = "start {prep}half of a note{/prep} end"
    edit = et.wrap(text, *sel(text, "start {prep}half"), "secret")
    assert apply(text, edit) == "{secret}start {prep}half of a note{/prep}{/secret} end"
    assert clean(apply(text, edit))


def test_selection_inside_another_kind_nests() -> None:
    text = "{revealed [[S]]}known part{/revealed}"
    edit = et.wrap(text, *sel(text, "part"), "secret")
    assert apply(text, edit) == "{revealed [[S]]}known {secret}part{/secret}{/revealed}"


def test_already_inside_and_heading_crossings_are_refused() -> None:
    text = "{secret}all of this{/secret}"
    assert "already" in et.wrap(text, *sel(text, "of"), "secret").notice
    text = "para one\n## Next\npara two"
    assert "heading" in et.wrap(text, *sel(text, "one\n## Next\npara"), "secret").notice


def test_hiding_revealed_text_asks_first() -> None:
    text = "x {revealed [[S]]}known{/revealed} y"
    question = et.wrap(text, *sel(text, "x {revealed [[S]]}kn"), "secret")
    assert question.confirm and question.insert is None
    edit = et.wrap(text, *sel(text, "x {revealed [[S]]}kn"), "secret", confirmed=True)
    assert apply(text, edit) == "{secret}x {revealed [[S]]}known{/revealed}{/secret} y"


def test_multi_line_wrap_is_valid_markup() -> None:
    text = "- one\n- two\n- three"
    after = apply(text, et.wrap(text, *sel(text, "one\n- two"), "secret"))
    assert after == "- {secret}one\n- two{/secret}\n- three" and clean(after)


def test_positions_are_utf16_for_the_browser() -> None:
    text = "🐉 dragons are not extinct"
    a, b = sel(text, "not extinct")
    assert (a, b) == (len("🐉 dragons are ") + 1, len("🐉 dragons are not extinct") + 1)
    edit = et.wrap(text, a, b, "secret")
    assert apply(text, edit) == "🐉 dragons are {secret}not extinct{/secret}"
    assert selected(text, edit) == "not extinct"


# -- unmark, reveal, sections ---------------------------------------------------------------------


def test_unmark_whole_tag_or_split_it() -> None:
    text = "{secret reveal=g}The lazy fox jumps{/secret}"
    assert apply(text, et.unmark(text, *sel(text, "lazy"))) == (
        "{secret reveal=g}The {/secret}lazy{secret reveal=g} fox jumps{/secret}"
    )
    cursor = sel(text, "lazy")[0]
    assert apply(text, et.unmark(text, cursor, cursor)) == "The lazy fox jumps"
    assert "inside a tag" in et.unmark("plain", 1, 1).notice


def test_unmark_targets_the_innermost_tag() -> None:
    text = "{secret}a {prep}b{/prep} c{/secret}"
    cursor = sel(text, "b")[0]
    assert apply(text, et.unmark(text, cursor, cursor)) == "{secret}a b c{/secret}"


def test_reveal_turns_the_secret_at_the_cursor_into_a_reveal() -> None:
    text = "x {secret reveal=vex about=[[Plot]]}the letter{/secret} y"
    cursor = sel(text, "letter")[0]
    edit = et.reveal(text, cursor, cursor, "[[Session 7#Notes]]")
    assert apply(text, edit) == (
        "x {revealed [[Session 7#Notes]] reveal=vex about=[[Plot]]}the letter{/revealed} y"
    )


def test_reveal_without_a_secret_wraps_the_selection() -> None:
    text = "public now"
    edit = et.reveal(text, *sel(text, "now"), "[[Session 2]]")
    assert apply(text, edit) == "public {revealed [[Session 2]]}now{/revealed}"


def test_section_secret_toggles() -> None:
    text = "## Plans\nThe plan.\n## Other\nx"
    cursor = sel(text, "plan.")[0]
    secret = apply(text, et.heading_secret(text, cursor))
    assert secret == "## Plans {secret}\nThe plan.\n## Other\nx"
    with_args = "## Plans {secret reveal=g}\nThe plan."
    assert apply(with_args, et.heading_secret(with_args, 12)) == "## Plans\nThe plan."
    assert "under a ## heading" in et.heading_secret("no heading", 2).notice


def test_links_and_embeds() -> None:
    text = "meet the twin here"
    assert apply(text, et.insert_link(text, *sel(text, "the twin"), "Cassie Ferndance")) == (
        "meet [[Cassie Ferndance|the twin]] here"
    )
    assert apply("x", et.insert_link("x", 1, 1, "Valoria")) == "x[[Valoria]]"
    assert apply("", et.insert_embed("", 0, 0, "map-1", "image")) == "![[img:map-1]]"
    assert apply("", et.insert_embed("", 0, 0, "voice", "audio")) == "[[audio:voice]]"


# -- quick fixes ----------------------------------------------------------------------------------


BROKEN = "{secret}starts here\nand goes on\n\nnext paragraph  \n## Next\nlater"


def test_close_at_end_of_section_or_paragraph() -> None:
    section = apply(BROKEN, et.close_unclosed(BROKEN, 0, 0, at="section"))
    assert "next paragraph{/secret}  \n## Next" in section and clean(section)
    paragraph = apply(BROKEN, et.close_unclosed(BROKEN, 0, 0, at="paragraph"))
    assert "and goes on{/secret}\n\nnext" in paragraph and clean(paragraph)


def test_remove_opener_or_stray_closer() -> None:
    assert apply(BROKEN, et.remove_tag(BROKEN, 0, 0)).startswith("starts here")
    stray = "text {/prep} more"
    assert apply(stray, et.remove_tag(stray, 0, 5)) == "text  more"


@pytest.mark.parametrize(
    "edit",
    [
        lambda: et.close_unclosed(BROKEN, 0, 3, at="section"),
        lambda: et.close_unclosed("x {/secret}", 0, 2, at="section"),
        lambda: et.remove_tag(BROKEN, 99, 0),
    ],
)
def test_fixes_notice_when_the_text_changed(edit: object) -> None:
    assert "no longer there" in edit().notice  # type: ignore[operator]


def test_find_spans_skips_code_and_stops_at_headings() -> None:
    text = "```\n{secret}not a tag\n```\n{prep}open\n## H\n{secret}x{/secret}"
    spans = et.find_spans(text)
    assert [(s.kind, s.closed) for s in spans] == [("prep", False), ("secret", True)]
