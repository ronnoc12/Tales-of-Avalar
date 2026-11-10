"""One test per validator error type."""

from __future__ import annotations

import pytest

from chronicler.campaign import Campaign
from chronicler.validate import Issue, validate
from tests.conftest import MakeCampaign, entry


def codes(issues: list[Issue]) -> set[str]:
    return {i.code for i in issues}


def test_fixture_campaign_is_clean(fixture_campaign: Campaign) -> None:
    assert validate(fixture_campaign) == []


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ({"npcs/a.md": "no frontmatter"}, "no-frontmatter"),
        ({"npcs/a.md": "---\nid: [bad\n---\n"}, "yaml-error"),
        ({"npcs/a.md": "---\nid: a\nname: A\n---\n"}, "missing-type"),
        ({"npcs/a.md": entry("a", "spaceship")}, "unknown-type"),
        ({"locations/a.md": entry("a", "npc")}, "wrong-folder"),
        ({"npcs/a.md": entry("a", "npc", disposition="11")}, "schema"),
        ({"npcs/a.md": entry("a", "npc", favourite_food="pie")}, "unknown-field"),
        ({"npcs/a.md": entry("a", "npc", secret_fields="[age]")}, "secret-field-missing"),
        ({"npcs/a.md": entry("a", "npc", party_view="{ mood: grim }")}, "unknown-field"),
        ({"npcs/a.md": entry("a", "npc", "{secret}never closed")}, "unclosed-span"),
        ({"npcs/a.md": entry("a", "npc", "x {/secret}")}, "unbalanced-span"),
        ({"npcs/a.md": entry("a", "npc", "{secret}{prep}x{/prep}{/secret}")}, "nested-span"),
        ({"npcs/a.md": entry("a", "npc", "{spoiler}x")}, "unknown-tag"),
        ({"npcs/a.md": entry("a", "npc", "## Head {secret}x\n")}, "unclosed-span"),
        ({"npcs/a.md": entry("a", "npc", self_view='"{secret}x"')}, "unclosed-span"),
        ({"npcs/a.md": entry("a", "npc", "see [[Nobody]]")}, "broken-link"),
        ({"npcs/a.md": entry("a", "npc", "see [[a#Nope]]")}, "missing-section"),
        ({"npcs/a.md": entry("a", "npc", "![[img:nope]]")}, "missing-asset"),
        ({"npcs/a.md": entry("a", "npc", parent="Somewhere")}, "bad-parent"),
        (
            {"plotlines/p.md": entry("p", "plotline", actions="[{ name: X, date: someday }]")},
            "bad-date",
        ),
        ({"lore/l.md": entry("l", "lore", date="the old days")}, "bad-date"),
        ({"assets/x.md": entry("x", "asset", file="missing.png")}, "asset-missing"),
        ({"calendar.yaml": "epoch_weekday: 99\n"}, "config"),
        ({"calendar.yaml": "months: [unclosed\n"}, "config"),
        ({"campaign.yaml": "name: [unclosed\n"}, "config"),
    ],
)
def test_error_types(make_campaign: MakeCampaign, files: dict[str, str], code: str) -> None:
    assert code in codes(validate(make_campaign(files)))


def test_duplicate_id(make_campaign: MakeCampaign) -> None:
    issues = validate(
        make_campaign({"npcs/a.md": entry("same", "npc"), "npcs/b.md": entry("same", "npc")})
    )
    duplicate = next(i for i in issues if i.code == "duplicate-id")
    assert duplicate.file == "npcs/b.md" and "npcs/a.md" in duplicate.message


def test_duplicate_name_and_ambiguous_link(make_campaign: MakeCampaign) -> None:
    issues = validate(
        make_campaign(
            {
                "npcs/a.md": entry("a", "npc", name="Lyra"),
                "npcs/b.md": entry("b", "npc", name="Lyra"),
                "npcs/c.md": entry("c", "npc", "Ask [[Lyra]]"),
            }
        )
    )
    assert {"duplicate-name", "ambiguous-link"} <= codes(issues)


def test_parent_cycle(make_campaign: MakeCampaign) -> None:
    issues = validate(
        make_campaign(
            {
                "locations/x.md": entry("x", "location", parent='"[[y]]"'),
                "locations/y.md": entry("y", "location", parent='"[[x]]"'),
            }
        )
    )
    assert "parent-cycle" in codes(issues)


def test_canon_linking_to_retired(make_campaign: MakeCampaign) -> None:
    issues = validate(
        make_campaign(
            {
                "lore/old.md": entry("old", "lore", status="retired"),
                "npcs/a.md": entry("a", "npc", "Remember [[old]]"),
                "npcs/b.md": entry("b", "npc", "Remember [[old]]", status="draft"),
            }
        )
    )
    warnings = [i for i in issues if i.code == "links-non-canon"]
    assert [i.entry_id for i in warnings] == ["a"]
    assert warnings[0].level == "warning"


def test_broken_link_suggests_spelling(make_campaign: MakeCampaign) -> None:
    issues = validate(
        make_campaign(
            {
                "npcs/v.md": entry("vhallion", "npc", name="Vhallion"),
                "npcs/a.md": entry("a", "npc", "see [[Vhalion]]"),
            }
        )
    )
    broken = next(i for i in issues if i.code == "broken-link")
    assert "did you mean: Vhallion" in broken.message and broken.line == 6


def test_issue_format() -> None:
    issue = Issue("error", "x", "msg", "npcs/a.md", 3, "a", "field")
    assert issue.format() == "npcs/a.md:3: error: msg [field] (x)"


def test_warns_when_visible_text_names_hidden_entry(make_campaign: MakeCampaign) -> None:
    issues = validate(
        make_campaign(
            {
                "npcs/a.md": entry(
                    "a", "npc", "Ally of [[Hidden One]]. {secret}[[Hidden One]]{/secret}"
                ),
                "npcs/h.md": entry("h", "npc", name="Hidden One", secret="true"),
            }
        )
    )
    warnings = [i for i in issues if i.code == "shows-hidden-name"]
    assert len(warnings) == 1 and warnings[0].entry_id == "a"


def test_warns_when_id_reveals_hidden_name(make_campaign: MakeCampaign) -> None:
    issues = validate(
        make_campaign(
            {
                "npcs/a.md": entry(
                    "lyzandral", "npc", name="Lyzandral", party_view="{ name: Mother }"
                ),
                "npcs/b.md": entry(
                    "mother", "npc", name="Lyzandral2", party_view="{ name: Mother }"
                ),
            }
        )
    )
    flagged = [i.entry_id for i in issues if i.code == "id-reveals-name"]
    assert flagged == ["lyzandral"]
