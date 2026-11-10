from __future__ import annotations

from chronicler.campaign import Campaign


def test_finds_every_reference_with_snippets(fixture_campaign: Campaign) -> None:
    result = fixture_campaign.gm.search.search("Midcross")
    ids = [h.entry_id for h in result.hits]
    assert ids[0] == "midcross"  # exact name match ranks first
    assert {"casfer-ferndance", "lord-burgenroot", "session-001"} <= set(ids)
    hit = next(h for h in result.hits if h.entry_id == "lord-burgenroot")
    assert any(s.match.lower() == "midcross" for s in hit.snippets)


def test_all_terms_must_match(fixture_campaign: Campaign) -> None:
    ids = {h.entry_id for h in fixture_campaign.gm.search.search("bad jokes").hits}
    assert ids == {"casfer-ferndance"}


def test_type_filter(fixture_campaign: Campaign) -> None:
    hits = fixture_campaign.gm.search.search("Midcross", entry_type="npc").hits
    assert hits and all(h.type == "npc" for h in hits)


def test_gm_search_includes_secrets(fixture_campaign: Campaign) -> None:
    assert fixture_campaign.gm.search.search("CANARY-SECRET-DEALINGS").hits


def test_player_search_never_finds_secrets(fixture_campaign: Campaign) -> None:
    for query in ("canary", "CANARY-SECRET-DEALINGS", "Vhallion", "Faythe", "bridge is out"):
        assert fixture_campaign.player.search.search(query).hits == [], query


def test_suggestions_when_nothing_found(fixture_campaign: Campaign) -> None:
    result = fixture_campaign.gm.search.search("Casfre Ferndnce")
    assert not result.hits and "Casfer Ferndance" in result.suggestions


def test_alias_and_search_only_alias(fixture_campaign: Campaign) -> None:
    assert fixture_campaign.gm.search.search("Casfr").hits[0].entry_id == "casfer-ferndance"
    assert fixture_campaign.player.search.search("Casfr").hits[0].entry_id == "casfer-ferndance"


def test_empty_query(fixture_campaign: Campaign) -> None:
    assert fixture_campaign.gm.search.search("   ").hits == []


def test_snippet_links_to_section(fixture_campaign: Campaign) -> None:
    hit = fixture_campaign.gm.search.search("bad jokes").hits[0]
    assert {s.section_slug for s in hit.snippets} >= {"personality"}
