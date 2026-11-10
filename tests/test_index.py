from __future__ import annotations

from chronicler.campaign import Campaign
from chronicler.index import CampaignIndex
from tests.conftest import MakeCampaign, entry


def test_resolve_by_name_alias_id_and_case(fixture_campaign: Campaign) -> None:
    index = fixture_campaign.gm.index
    for text in ("Casfer Ferndance", "cas", "casfer-ferndance", "[[Cas]]", "CASFR"):
        assert index.resolve(text).entry_id == "casfer-ferndance", text


def test_resolve_sections_and_places(fixture_campaign: Campaign) -> None:
    index = fixture_campaign.gm.index
    governance = index.resolve("Midcross#Governance")
    assert (governance.entry_id, governance.section_slug, governance.place_key) == (
        "midcross",
        "governance",
        None,
    )
    place = index.resolve("The Gilded Anchor")
    assert place.place_key == "midcross#the-gilded-anchor" and place.entry_id == "midcross"
    via_section = index.resolve("Midcross#The Gilded Anchor")
    assert via_section.place_key == "midcross#the-gilded-anchor"
    assert index.resolve("Midcross#Nope").status == "missing-section"


def test_fuzzy_suggestions_for_misspellings(fixture_campaign: Campaign) -> None:
    result = fixture_campaign.gm.index.resolve("Burgenrot")
    assert result.status == "missing"
    assert "Lord Burgenroot" in result.suggestions or "Burgen Root" in result.suggestions


def test_ambiguous_names(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign(
        {
            "npcs/a.md": entry("elara-a", "npc", name="Elara"),
            "npcs/b.md": entry("elara-b", "npc", name="Elara"),
        }
    )
    result = campaign.gm.index.resolve("Elara")
    assert result.status == "ambiguous" and len(result.candidates) == 2
    assert campaign.gm.index.resolve("elara-a").entry_id == "elara-a"


def test_computed_relations(fixture_campaign: Campaign) -> None:
    index = fixture_campaign.gm.index
    assert set(index.children["the-continent"]) == {"midcross", "valoria"}
    assert index.parent_of["midcross"] == "the-continent"
    assert {m.source_id for m in index.members["use"]} == {"casfer-ferndance", "cassie-ferndance"}
    assert {m.source_id for m in index.presence["use"]} == {"midcross"}
    assert {m.source_id for m in index.cast["ravenwood-silence"]} == {
        "casfer-ferndance",
        "midcross",
    }
    assert set(index.here["midcross"]) == {"casfer-ferndance", "lord-burgenroot"}
    assert {m.source_id for m in index.incoming["cassie-ferndance"]} == {"casfer-ferndance"}
    assert index.decisions_for["ravenwood-silence"] == ["spare-the-scout"]
    assert [p.name for p in index.places_of["midcross"]] == ["The Gilded Anchor", "Smugglers' Den"]


def test_backlinks_and_spans(fixture_campaign: Campaign) -> None:
    refs = fixture_campaign.gm.index.backlinks["vhallion"]
    assert {r.source_id for r in refs} == {"casfer-ferndance", "king-alaric-iii"}
    casfer_ref = next(r for r in refs if r.source_id == "casfer-ferndance")
    assert "secret-section" in casfer_ref.spans and casfer_ref.line is not None
    alaric_ref = next(r for r in refs if r.source_id == "king-alaric-iii")
    assert alaric_ref.spans == ("secret",)


def test_frontmatter_references_have_field_paths(fixture_campaign: Campaign) -> None:
    refs = fixture_campaign.gm.index.backlinks["use"]
    fields = {r.field for r in refs if r.source_id == "casfer-ferndance"}
    assert "factions[0].faction" in fields


def test_unlinked_mentions(fixture_campaign: Campaign) -> None:
    mentions = fixture_campaign.gm.index.mentions()
    found = {(m.source_id, m.target_id) for m in mentions}
    assert ("valoria", "casfer-ferndance") in found  # "Casfer Ferndance visits often."
    assert all(m.source_id != m.target_id for m in mentions)
    assert fixture_campaign.gm.index.mentions() is mentions  # cached


def test_tree_order_and_cycles(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign(
        {
            "locations/a.md": entry("a", "location", name="A"),
            "locations/b.md": entry("b", "location", name="B", parent='"[[A]]"'),
            "locations/c.md": entry("c", "location", name="C", parent='"[[B]]"'),
            "locations/x.md": entry("x", "location", name="X", parent='"[[Y]]"'),
            "locations/y.md": entry("y", "location", name="Y", parent='"[[X]]"'),
        }
    )
    tree = [(d.id, depth) for d, depth in campaign.gm.index.tree("location")]
    assert tree[:3] == [("a", 0), ("b", 1), ("c", 2)]
    assert {i for i, _ in tree} == {"a", "b", "c", "x", "y"}


def test_duplicate_ids_keep_first() -> None:
    from chronicler.document import parse_document

    docs = [
        parse_document("---\nid: same\nname: One\n---\n", "npcs/one.md"),
        parse_document("---\nid: same\nname: Two\n---\n", "npcs/two.md"),
    ]
    index = CampaignIndex(docs)
    assert index.docs["same"].name == "One"
    assert index.duplicate_ids == {"same": ["npcs/one.md", "npcs/two.md"]}


def test_self_section_link(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign({"npcs/a.md": entry("a", "npc", "## Notes\nsee [[#Notes]]")})
    ref = next(r for r in campaign.gm.index.references if r.raw == "[[#Notes]]")
    assert ref.resolution.entry_id == "a" and ref.resolution.section_slug == "notes"
    assert "a" not in campaign.gm.index.backlinks


def test_templates_folder_is_ignored(fixture_campaign: Campaign) -> None:
    assert "should-be-ignored" not in fixture_campaign.gm.index.docs
