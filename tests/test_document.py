from __future__ import annotations

from chronicler.document import parse_document, slugify, split_frontmatter


def test_slugify() -> None:
    assert slugify("Lord Burgenroot's Hall") == "lord-burgenroots-hall"
    assert slugify("The [[Gilded Anchor|Anchor]]") == "the-anchor"
    assert slugify("Visit [[Midcross#Docks]]") == "visit-midcross"
    assert slugify("Café Déjà") == "cafe-deja"
    assert slugify("!!!") == "section"


def test_split_frontmatter() -> None:
    front, body, line = split_frontmatter("---\na: 1\n---\nbody")
    assert (front, body, line) == ("a: 1", "body", 4)
    assert split_frontmatter("no frontmatter")[0] is None
    assert split_frontmatter("---\nunclosed")[0] is None


def test_sections_levels_attrs_and_secret_inheritance() -> None:
    text = "\n".join(
        [
            "---",
            "id: x",
            "type: npc",
            "name: X",
            "---",
            "Intro line",
            "## Public",
            "text",
            "## Hidden {secret}",
            "### Child",
            "child text",
            "## Places",
            "### The Gilded Anchor {inn}",
            "### Den {secret}",
            "#### Deeper",
            "## After",
        ]
    )
    doc = parse_document(text, "npcs/x.md")
    by_slug = {s.slug: s for s in doc.sections}
    assert doc.sections[0].level == 0 and doc.sections[0].lines == ["Intro line"]
    assert (
        by_slug["hidden"].own_secret and by_slug["child"].secret and not by_slug["child"].own_secret
    )
    assert not by_slug["after"].secret and not by_slug["public"].secret
    assert by_slug["the-gilded-anchor"].is_place and by_slug["the-gilded-anchor"].kind == "inn"
    assert by_slug["den"].is_place and by_slug["den"].secret
    assert not by_slug["deeper"].is_place and by_slug["deeper"].secret
    assert by_slug["public"].line == 7 and by_slug["public"].content_line(0) == 8


def test_duplicate_headings_get_unique_slugs() -> None:
    doc = parse_document("---\nid: x\n---\n## Notes\n## Notes", "x.md")
    assert [s.slug for s in doc.sections] == ["notes", "notes-2"]


def test_headings_inside_code_fences_are_text() -> None:
    doc = parse_document("---\nid: x\n---\n```\n## not a heading\n```\n", "x.md")
    assert len(doc.sections) == 1 and doc.sections[0].code == [True, True, True, False]


def test_bad_yaml_and_missing_frontmatter_are_recorded() -> None:
    assert parse_document("no front", "a.md").issues[0].code == "no-frontmatter"
    bad = parse_document("---\nid: [unclosed\n---\n", "a.md")
    assert bad.issues[0].code == "yaml-error" and bad.meta == {}
    listy = parse_document("---\n- a\n---\n", "a.md")
    assert listy.issues[0].code == "yaml-error"


def test_reserved_placeholder_characters_are_stripped() -> None:
    doc = parse_document("---\nid: x\n---\nhithere", "x.md")
    assert doc.sections[0].lines == ["hithere"]


def test_windows_newlines() -> None:
    doc = parse_document("---\r\nid: x\r\nname: X\r\n---\r\n## A\r\ntext\r\n", "x.md")
    assert doc.name == "X" and doc.sections[0].title == "A"
