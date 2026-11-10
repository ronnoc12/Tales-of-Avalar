from __future__ import annotations

from decimal import Decimal

from chronicler import home
from chronicler.calendar import WorldDate
from chronicler.campaign import Campaign
from tests.conftest import MakeCampaign, entry


def test_current_date_from_last_session(fixture_campaign: Campaign) -> None:
    today = home.current_date(fixture_campaign.gm, fixture_campaign.calendar)
    assert today == WorldDate(1500, 2, 10)


def test_current_date_config_override(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign({"calendar.yaml": "current_date: 5 Month-2 1600 AS\n"})
    assert home.current_date(campaign.gm, campaign.calendar) == WorldDate(1600, 2, 5)


def test_no_current_date(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign({"npcs/a.md": entry("a", "npc")})
    assert home.current_date(campaign.gm, campaign.calendar) is None
    assert home.last_session(campaign.gm) is None


def test_last_session(fixture_campaign: Campaign) -> None:
    session = home.last_session(fixture_campaign.gm)
    assert session is not None and session.id == "session-002"


def test_upcoming_and_overdue(fixture_campaign: Campaign) -> None:
    today = WorldDate(1500, 2, 10)
    soon, overdue = home.upcoming(fixture_campaign.gm, fixture_campaign.calendar, today)
    assert [(u.title, u.days_away, u.kind) for u in soon] == [
        ("Spare the goblin scout?", 2, "deadline"),
        ("Mill burns", 4, "action"),
    ]
    assert soon[1].date_text == "Moonday, 14 Seedmoon 1500 AS"
    assert [u.title for u in overdue] == ["Scouts vanish"]  # resolved actions are skipped


def test_player_view_sees_no_plot_actions(fixture_campaign: Campaign) -> None:
    soon, overdue = home.upcoming(
        fixture_campaign.player, fixture_campaign.calendar, WorldDate(1500, 2, 10)
    )
    assert soon == [] and overdue == []


def test_hanging_threads(fixture_campaign: Campaign) -> None:
    threads = {(t.entry_id, t.kind) for t in home.hanging_threads(fixture_campaign.gm)}
    assert ("ravenwood-silence", "plotline") in threads
    assert ("spare-the-scout", "decision") in threads
    assert ("haldirs-glaive", "item") in threads


def test_prep_notes_and_tasks(fixture_campaign: Campaign) -> None:
    notes = home.prep_notes(fixture_campaign.gm)
    assert [(n.entry_id, n.anchor, n.text) for n in notes] == [
        ("midcross", "prep-1", "Remind them the bridge is out")
    ]
    tasks = home.prep_tasks(fixture_campaign.gm)
    assert [(t.entry_id, t.done, t.total) for t in tasks] == [("ashvault-dungeon", 1, 3)]
    assert home.prep_notes(fixture_campaign.player) == []


def test_prep_anchor_matches_renderer(fixture_campaign: Campaign) -> None:
    note = home.prep_notes(fixture_campaign.gm)[0]
    html = fixture_campaign.gm.renderer.body(fixture_campaign.gm.index.docs[note.entry_id])
    assert f'id="{note.anchor}">{note.text}' in html


def test_spotlight_most_neglected_first(fixture_campaign: Campaign) -> None:
    rows = home.spotlight(fixture_campaign.gm)
    assert [(r.name, r.sessions_since) for r in rows] == [
        ("Quinn", None),
        ("Thorne", 1),
        ("Mira", 0),
    ]
    assert rows[0].player == "Jo" and rows[0].total_sessions == 2


def test_spotlight_falls_back_to_links_without_list(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign(
        {
            "pcs/a.md": entry("a", "pc", name="Ash"),
            "sessions/s1.md": entry("s1", "session", "Ash shone: [[Ash]]", number="1"),
            "sessions/s2.md": entry("s2", "session", "quiet", number="2"),
        }
    )
    assert [(r.name, r.sessions_since) for r in home.spotlight(campaign.gm)] == [("Ash", 1)]


def test_disposition_trend(fixture_campaign: Campaign) -> None:
    trend = home.score_trend(fixture_campaign.gm, "casfer-ferndance")
    assert trend is not None and trend.total == Decimal("0.75") and trend.sessions == 2
    assert trend.label == "warming +0.75 over the last 2 session(s)"
    assert home.score_trend(fixture_campaign.gm, "cassie-ferndance") is None
    assert home.Trend(Decimal("-1.00"), 3).label.startswith("cooling -1.00")
