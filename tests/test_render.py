from __future__ import annotations

from chronicler.campaign import Campaign
from tests.conftest import MakeCampaign, entry


def test_gm_body_marks_secrets_revealed_and_prep(fixture_campaign: Campaign) -> None:
    renderer = fixture_campaign.gm.renderer
    html = renderer.body(fixture_campaign.gm.index.docs["midcross"])
    assert '<span class="secret" title="GM only">CANARY-INLINE-GOVERNANCE</span>' in html
    assert 'class="revealed" title="Revealed in Session 1"' in html
    assert '<mark class="prep" id="prep-1">Remind them the bridge is out</mark>' in html
    assert 'class="entry-section secret-section" id="the-undercity"' in html
    assert 'href="/place/midcross/the-gilded-anchor"' in html
    assert '<span class="badge">inn</span>' in html


def test_player_body_has_no_secret_markup(fixture_campaign: Campaign) -> None:
    html = fixture_campaign.player.renderer.body(fixture_campaign.player.index.docs["midcross"])
    assert "secret" not in html and "prep" not in html and "CANARY" not in html
    assert "The council can remove him." in html


def test_links_resolve_and_broken_links_hint(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign(
        {
            "npcs/a.md": entry("a", "npc", "Meet [[Bee|the bee]] and [[Beee]]."),
            "npcs/b.md": entry("b", "npc", name="Bee"),
        }
    )
    gm = campaign.gm.renderer.body(campaign.gm.index.docs["a"])
    assert '<a class="link" href="/entry/b">the bee</a>' in gm
    assert 'class="link-broken" title="did you mean: Bee"' in gm


def test_player_broken_links_are_plain_text(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign(
        {
            "npcs/a.md": entry("a", "npc", "Ally of [[Hidden One]]."),
            "npcs/h.md": entry("h", "npc", name="Hidden One", secret="true"),
        }
    )
    html = campaign.player.renderer.body(campaign.player.index.docs["a"])
    assert "Ally of Hidden One." in html and "link" not in html and "did you mean" not in html


def test_raw_html_is_escaped(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign({"npcs/a.md": entry("a", "npc", "<script>alert(1)</script> **bold**")})
    html = campaign.gm.renderer.body(campaign.gm.index.docs["a"])
    assert "<script>" not in html and "&lt;script&gt;" in html and "<strong>bold</strong>" in html


def test_task_lists_render_as_boxes(fixture_campaign: Campaign) -> None:
    html = fixture_campaign.gm.renderer.body(fixture_campaign.gm.index.docs["ashvault-dungeon"])
    assert "☑ Draw the map" in html and "☐ Stat the guardian" in html


def test_embeds(fixture_campaign: Campaign) -> None:
    html = fixture_campaign.gm.renderer.inline("![[img:casfer-portrait]]", "casfer-ferndance")
    assert '<img class="embed-img" src="/asset/casfer-portrait" alt="Casfer Ferndance"' in html
    missing = fixture_campaign.gm.renderer.inline("![[img:nope]]", "casfer-ferndance")
    assert "link-broken" in missing
    assert fixture_campaign.player.renderer.inline("![[img:nope]]", "casfer-ferndance") == ""


def test_audio_embed(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign(
        {
            "assets/v.md": entry("voice", "asset", kind="audio", file="v.ogg"),
            "assets/files/v.ogg": "x",
            "npcs/a.md": entry("a", "npc", "[[audio:voice]]"),
        }
    )
    html = campaign.gm.renderer.body(campaign.gm.index.docs["a"])
    assert '<audio class="embed-audio" controls' in html


def test_values_scores_dates_and_secret_items(fixture_campaign: Campaign) -> None:
    renderer = fixture_campaign.gm.renderer
    assert renderer.value(2.5, "x", "disposition") == '<span class="score score-pos">+2.50</span>'
    assert "score-neg" in renderer.value(-1, "x", "ruler_approval")
    assert "score-zero" in renderer.value(0, "x", "reputation")
    date = renderer.value("10 Seedmoon 1500 AS", "x", "date")
    assert date == '<span class="world-date">Thunderday, 10 Seedmoon 1500 AS</span>'
    assert renderer.value("c. 300 AS", "x", "date") == '<span class="world-date">c. 300 AS</span>'
    assert renderer.value(True, "x") == "Yes"
    item = renderer.value({"text": "Hide", "secret": True}, "x", "goals")
    assert 'class="kv-group secret"' in item and '<span class="kv-primary">Hide</span>' in item
    assert renderer.value(["a", "b"], "x") == "a, b"


def test_place_body(fixture_campaign: Campaign) -> None:
    doc = fixture_campaign.gm.index.docs["midcross"]
    html = fixture_campaign.gm.renderer.place_body(doc, "the-gilded-anchor")
    assert "Cheap rooms" in html and "CANARY" not in html
    assert fixture_campaign.gm.renderer.place_body(doc, "nope") == ""
