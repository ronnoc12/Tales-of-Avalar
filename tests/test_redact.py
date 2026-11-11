"""Redaction is the most important safety property: nothing secret may reach players."""

from __future__ import annotations

import json
from typing import Any

from chronicler.campaign import Campaign
from chronicler.document import parse_document
from chronicler.redact import (
    DROP,
    find_mismatches,
    is_player_visible,
    player_documents,
    redact_document,
    redact_meta,
    redact_value,
)
from tests.conftest import CANARY, MakeCampaign, entry


def _dump(campaign: Campaign) -> str:
    """Every byte of text in the Player view's documents."""
    parts: list[str] = []
    for doc in campaign.player.documents:
        parts.append(json.dumps(doc.meta, default=str))
        for section in doc.sections:
            parts.append(section.title)
            parts.extend(section.lines)
    return "\n".join(parts)


def test_no_canary_in_any_player_document(fixture_campaign: Campaign) -> None:
    assert CANARY not in _dump(fixture_campaign)


def test_fixture_actually_contains_canaries_for_gm(fixture_campaign: Campaign) -> None:
    gm_text = "\n".join(
        json.dumps(d.meta, default=str) + "\n".join(line for s in d.sections for line in s.lines)
        for d in fixture_campaign.gm.documents
    )
    assert gm_text.count(CANARY) >= 25


def test_hidden_entries(fixture_campaign: Campaign) -> None:
    visible = {d.id for d in fixture_campaign.player.documents}
    for hidden in ("vhallion", "thorne", "mira", "spare-the-scout", "ashvault-dungeon", "aetheria"):
        assert hidden not in visible
    for shown in ("casfer-ferndance", "midcross", "use", "ravenwood-silence", "session-001"):
        assert shown in visible


def test_party_view_overrides_and_gm_only_fields(fixture_campaign: Campaign) -> None:
    casfer = fixture_campaign.player.index.docs["casfer-ferndance"].meta
    assert casfer["role"] == "Runs a consulting firm"
    for gm_only in ("disposition", "party_role", "self_view", "plots", "portrayal", "party_view"):
        assert gm_only not in casfer
    assert all("standing" not in f for f in casfer["factions"])
    assert [g["text"] for g in casfer["goals"]] == ["Keep USE neutral"]
    assert len(casfer["relationships"]) == 1


def test_party_view_name_replaces_true_name(fixture_campaign: Campaign) -> None:
    guardians = fixture_campaign.player.index.docs["the-teeth"]
    assert guardians.name == "The Teeth"


def test_life_state_belief(fixture_campaign: Campaign) -> None:
    assert fixture_campaign.gm.index.docs["haldir"].meta["life_state"] == "dead"
    assert fixture_campaign.player.index.docs["haldir"].meta["life_state"] == "alive"


def test_secret_sections_places_and_inline(fixture_campaign: Campaign) -> None:
    midcross = fixture_campaign.player.index.docs["midcross"]
    slugs = [s.slug for s in midcross.sections]
    assert "the-undercity" not in slugs and "deeper-vaults" not in slugs
    assert "smugglers-den" not in slugs and "the-gilded-anchor" in slugs
    governance = next(s for s in midcross.sections if s.slug == "governance")
    assert "The council can remove him." in "\n".join(governance.lines)
    assert "{revealed" not in "\n".join(governance.lines)
    overview = next(s for s in midcross.sections if s.slug == "overview")
    assert "bridge is out" not in "\n".join(overview.lines)  # prep notes are GM-only


def test_redact_value_rules() -> None:
    assert redact_value("{secret}all{/secret}") is DROP
    assert redact_value("") == ""
    assert redact_value({"secret": True, "a": 1}) is DROP
    assert redact_value({"a": "{secret}x{/secret}"}) is DROP
    assert redact_value({"to": "[[X]]", "feeling": 3, "plans": "p"}) == {"to": "[[X]]"}
    assert redact_value([{"secret": True}, "ok"]) == ["ok"]
    assert redact_value([]) == []
    assert redact_value(5) == 5


def test_secret_fields_list() -> None:
    meta: dict[str, Any] = {
        "id": "x",
        "type": "npc",
        "name": "X",
        "race": "Elf",
        "age": 9,
        "secret_fields": ["age"],
    }
    assert redact_meta(meta) == {"id": "x", "type": "npc", "name": "X", "race": "Elf"}


def test_non_canon_entries_hidden() -> None:
    for status in ("draft", "idea", "alternate", "retired"):
        assert not is_player_visible({"type": "npc", "status": status})
    assert is_player_visible({"type": "npc"})


def test_redact_document_none_for_hidden() -> None:
    doc = parse_document("---\nid: x\ntype: pc\nname: X\n---\nhi", "pcs/x.md")
    assert redact_document(doc) is None
    assert player_documents([doc]).documents == []


def test_mismatches(fixture_campaign: Campaign) -> None:
    found = {(m.entry_id, m.field) for m in find_mismatches(fixture_campaign.documents)}
    assert ("haldir", "life_state") in found
    assert ("the-teeth", "name") in found
    assert ("casfer-ferndance", "role") in found


def test_player_index_cannot_resolve_hidden_names(fixture_campaign: Campaign) -> None:
    assert not fixture_campaign.player.index.resolve("Vhallion").ok
    assert fixture_campaign.player.index.suggest_names("Vhallion") == ()
    assert not fixture_campaign.player.index.resolve("Faythe's Guardians").ok
    assert fixture_campaign.player.index.resolve("The Teeth").ok


def test_player_backlinks_exclude_secret_sources(fixture_campaign: Campaign) -> None:
    sources = {
        r.source_id for r in fixture_campaign.player.index.backlinks.get("lord-burgenroot", [])
    }
    assert "casfer-ferndance" not in sources  # only linked from a secret relationship
    gm_sources = {r.source_id for r in fixture_campaign.gm.index.backlinks["lord-burgenroot"]}
    assert "casfer-ferndance" in gm_sources


def test_links_to_renamed_entries_use_party_name(fixture_campaign: Campaign) -> None:
    use = fixture_campaign.player.index.docs["use"]
    assert use.meta["relations"][0]["to"] == "[[The Teeth]]"
    resolution = fixture_campaign.player.index.resolve(use.meta["relations"][0]["to"])
    assert resolution.entry_id == "the-teeth"


def test_links_to_hidden_entries_become_plain_text_and_are_recorded(
    make_campaign: MakeCampaign,
) -> None:
    campaign = make_campaign(
        {
            "npcs/a.md": entry("a", "npc", "Ally of [[Hidden One]] at [[B#Den]]."),
            "npcs/h.md": entry("h", "npc", name="Hidden One", secret="true"),
            "npcs/b.md": entry("b", "npc", "## Places\n### Den {secret}\nx", name="B"),
        }
    )
    line = campaign.player.index.docs["a"].sections[0].lines[0]
    assert line == "Ally of Hidden One at B."
    assert {(leak.source_id, leak.target_id) for leak in campaign.hidden_name_leaks} == {
        ("a", "h"),
        ("a", "b"),
    }


def test_alignment_is_gm_only(fixture_campaign: Campaign) -> None:
    assert "alignment" in fixture_campaign.gm.index.docs["casfer-ferndance"].meta
    assert "alignment" not in fixture_campaign.player.index.docs["casfer-ferndance"].meta


def test_tags_are_gm_only(fixture_campaign: Campaign) -> None:
    assert fixture_campaign.gm.index.docs["casfer-ferndance"].meta["tags"] == ["use"]
    assert "tags" not in fixture_campaign.player.index.docs["casfer-ferndance"].meta


def test_multi_line_and_nested_spans_in_player_view(fixture_campaign: Campaign) -> None:
    doc = next(d for d in fixture_campaign.player.documents if d.id == "casfer-ferndance")
    notes = next(s for s in doc.sections if s.slug == "field-notes")
    text = "\n".join(notes.lines)
    assert "- Public bullet one.\n- Public bullet two." in text  # no empty bullets left behind
    assert "Known now: the twins" in text and "past." in text
    assert "```" not in text  # the code block inside the secret is gone too
    assert all(s.slug != "ledger" for s in doc.sections)
    assert len(notes.lines) == len(notes.code)
