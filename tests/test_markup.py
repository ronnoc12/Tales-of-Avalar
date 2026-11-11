from __future__ import annotations

import pytest

from chronicler.markup import (
    CONTINUE,
    Embed,
    Link,
    Span,
    Text,
    balance,
    emptied_by_redaction,
    iter_links,
    iter_spans,
    parse_args,
    parse_line,
    parse_link_body,
    parse_text,
    plain_text,
    redact_text,
    to_source,
)


def test_link_body_parts() -> None:
    assert parse_link_body("Vhallion#Weaknesses|his weak spot").target == "Vhallion"
    body = parse_link_body("Vhallion#Weaknesses|his weak spot")
    assert (body.section, body.label, body.display) == (
        "Weaknesses",
        "his weak spot",
        "his weak spot",
    )
    assert parse_link_body("Midcross").display == "Midcross"
    assert parse_link_body("#Notes").display == "Notes"


def test_parse_links_embeds_and_spans() -> None:
    nodes, issues = parse_line(
        "See [[Midcross]] ![[img:map]] [[audio:voice]] {secret}a [[B]]{/secret}."
    )
    assert not issues
    kinds = [type(n).__name__ for n in nodes]
    assert kinds == ["Text", "Link", "Text", "Embed", "Text", "Embed", "Text", "Span", "Text"]
    span = nodes[7]
    assert isinstance(span, Span) and span.kind == "secret"
    links = list(iter_links(nodes))
    assert [(link.target.target, spans) for link, spans in links] == [
        ("Midcross", ()),
        ("B", ("secret",)),
    ]
    embeds = [n for n in nodes if isinstance(n, Embed)]
    assert [(e.kind, e.ref) for e in embeds] == [("img", "map"), ("audio", "voice")]


def test_revealed_carries_argument() -> None:
    nodes, issues = parse_line("{revealed [[Session 7]]}They were twins.{/revealed}")
    assert not issues
    span = nodes[0]
    assert isinstance(span, Span) and span.kind == "revealed" and span.arg == "[[Session 7]]"


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("{secret}never closed", "unclosed-span"),
        ("stray {/secret}", "unbalanced-span"),
        ("{secret}a {prep}b{/secret} c{/prep}", "overlapping-spans"),
        ("{secret reveal=Bad_Group}x{/secret}", "bad-attr"),
        ("{secret about=Plot}x{/secret}", "bad-attr"),
        ("{prep reveal=x}y{/prep}", "unknown-attr"),
        ("{secret}a{/prep}", "unbalanced-span"),
        ("{spoiler}x{/spoiler}", "unknown-tag"),
    ],
)
def test_markup_problems_are_reported(line: str, code: str) -> None:
    _nodes, issues = parse_line(line)
    assert code in {i.code for i in issues}


def test_unknown_tag_is_only_a_warning() -> None:
    _nodes, issues = parse_line("{spoiler}x")
    assert {i.level for i in issues} == {"warning"}


def test_round_trip_source() -> None:
    line = "A [[X|y]] {revealed [[S]]}b{/revealed} {secret}c{/secret} {prep}d{/prep} ![[img:e]]"
    nodes, _ = parse_line(line)
    assert to_source(nodes) == line


def test_plain_text_uses_display_names() -> None:
    nodes, _ = parse_line("Meet [[Casfer Ferndance|Cas]] in {secret}[[Midcross]]{/secret}")
    assert plain_text(nodes) == "Meet Cas in Midcross"


def test_redaction_strips_secret_and_prep_and_unwraps_revealed() -> None:
    assert (
        redact_text("a {secret}S{/secret} b {prep}P{/prep} c {revealed x}R{/revealed}")
        == "a  b  c R"
    )


def test_redaction_fails_closed_on_unclosed_secret() -> None:
    assert redact_text("public {secret}hidden to the end") == "public "
    assert redact_text("public {revealed}not properly closed") == "public "


def test_redaction_is_per_line() -> None:
    assert redact_text("one {secret}x{/secret}\ntwo") == "one \ntwo"


def test_unclosed_outer_span_hides_everything_after_it() -> None:
    assert "CANARY" not in redact_text("{secret}CANARY {secret}more{/secret} tail")


def test_text_nodes_merge() -> None:
    nodes, _ = parse_line("plain {unknown} text")
    assert len(nodes) == 1 and isinstance(nodes[0], Text)


def test_bang_link_without_prefix_is_image() -> None:
    nodes, _ = parse_line("![[Midcross Map]]")
    assert isinstance(nodes[0], Embed) and nodes[0].kind == "img" and nodes[0].ref == "Midcross Map"


def test_unknown_prefix_is_a_normal_link() -> None:
    nodes, _ = parse_line("[[note:thing]]")
    assert isinstance(nodes[0], Link)


# -- nesting -----------------------------------------------------------------------------


def test_a_span_may_sit_fully_inside_another() -> None:
    nodes, issues = parse_line("{secret}a {prep}b{/prep} c{/secret}")
    assert not issues
    outer = nodes[0]
    assert isinstance(outer, Span) and [s.kind for s in iter_spans(nodes)] == ["secret", "prep"]
    assert to_source(nodes) == "{secret}a {prep}b{/prep} c{/secret}"


def test_player_sees_text_only_when_every_enclosing_span_is_visible() -> None:
    line = (
        "{revealed [[S]]}known {secret}CANARY-A{/secret} too{/revealed} "
        "{secret}x {revealed}CANARY-B{/revealed}{/secret}"
    )
    assert redact_text(line) == "known  too "


def test_overlapping_spans_fail_closed() -> None:
    assert "CANARY" not in redact_text("{secret}a {prep}b{/secret} CANARY{/prep} CANARY-tail")


# -- attributes ----------------------------------------------------------------------------


def test_opener_attributes() -> None:
    assert parse_args('[[Session 7#Notes]] reveal=vex-letter about=[[Velkyn Plot]] x="a b"') == (
        "[[Session 7#Notes]]",
        {"reveal": "vex-letter", "about": "[[Velkyn Plot]]", "x": "a b"},
    )
    nodes, issues = parse_line("{secret reveal=vex about=[[The Plot]]}x{/secret}")
    span = nodes[0]
    assert not issues and isinstance(span, Span)
    assert span.attrs == {"reveal": "vex", "about": "[[The Plot]]"}
    assert to_source(nodes) == "{secret reveal=vex about=[[The Plot]]}x{/secret}"


def test_built_opener_source_without_original_text() -> None:
    assert (
        Span("revealed", "[[S]]", attrs={"reveal": "g"}).opener_source()
        == "{revealed [[S]] reveal=g}"
    )


# -- multi-line spans ----------------------------------------------------------------------


def test_balance_closes_and_reopens_each_line() -> None:
    result = balance(["one {secret}two", "three", "", "four{/secret} five"])
    assert result.lines == [
        "one {secret}two{/secret}",
        f"{CONTINUE}{{secret}}three{{/secret}}",
        "",
        f"{CONTINUE}{{secret}}four{{/secret}} five",
    ]
    assert not result.issues
    assert all(not parse_line(line)[1] for line in result.lines)
    continued = [parse_line(line)[0] for line in result.lines[1:]]
    assert all(isinstance(n[0], Span) and n[0].continued for n in continued if n)


def test_balance_keeps_list_and_quote_markers_first() -> None:
    lines = balance(["- {prep}a", "- [ ] b", "> c", "1. d{/prep}"]).lines
    assert lines[1] == f"- [ ] {CONTINUE}{{prep}}b{{/prep}}"
    assert lines[2] == f"> {CONTINUE}{{prep}}c{{/prep}}"
    assert lines[3] == f"1. {CONTINUE}{{prep}}d{{/prep}}"


def test_balance_keeps_attributes_on_every_piece() -> None:
    lines = balance(["{secret reveal=g}a", "b{/secret}"]).lines
    assert lines[1] == f"{CONTINUE}{{secret reveal=g}}b{{/secret}}"


def test_code_lines_inside_hiding_spans_are_marked_hidden() -> None:
    result = balance(["{secret}a", "```", "code", "```", "b{/secret}"], [0, 1, 1, 1, 0])
    assert result.hidden == [False, True, True, True, False]
    assert result.lines[2] == "code"


def test_unclosed_span_is_reported_once_and_hides_the_rest() -> None:
    result = balance(["ok {secret}CANARY", "CANARY two", "CANARY three"])
    assert [(i, issue.code) for i, issue in result.issues] == [(0, "unclosed-span")]
    assert "CANARY" not in "\n".join(redact_text(line) for line in result.lines)


def test_unclosed_reveal_is_treated_as_secret() -> None:
    result = balance(["{revealed [[S]]}CANARY", "more"])
    assert result.lines[0].startswith("{secret}")
    assert redact_text("{revealed [[S]]}CANARY\nmore") == "\n"


def test_parse_text_reports_balance_issues_on_their_line() -> None:
    parsed = parse_text("fine\n{prep}never closed")
    assert parsed[0][1] == [] and [i.code for i in parsed[1][1]] == ["unclosed-span"]


def test_redact_multiline_frontmatter_value() -> None:
    assert redact_text("a {secret}CANARY\nCANARY{/secret} b") == "a \n b"


@pytest.mark.parametrize(
    ("original", "redacted", "emptied"),
    [
        ("- {secret}x{/secret}", "- ", True),
        ("{secret}x{/secret}", "", True),
        ("> {secret}x{/secret}", "> ", True),
        ("- a {secret}x{/secret}", "- a ", False),
        ("", "", False),
        ("- plain", "- plain", False),
    ],
)
def test_emptied_by_redaction(original: str, redacted: str, emptied: bool) -> None:
    assert emptied_by_redaction(original, redacted) is emptied
