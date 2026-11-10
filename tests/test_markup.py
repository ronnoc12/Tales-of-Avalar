from __future__ import annotations

import pytest

from chronicler.markup import (
    Embed,
    Link,
    Span,
    Text,
    iter_links,
    parse_line,
    parse_link_body,
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
        ("{secret}a {prep}b{/prep}{/secret}", "nested-span"),
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


def test_nested_open_is_kept_as_text_inside_span_then_stripped() -> None:
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
