"""Viewer tests: every page in both modes, plus a full Player-mode leak crawl."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chronicler.web.app import VIEW_COOKIE, create_app
from tests.conftest import CANARY, FIXTURE, MakeCampaign, entry


@pytest.fixture(scope="module")
def app_client() -> TestClient:
    return TestClient(create_app(FIXTURE))


def _client(app_client: TestClient, mode: str) -> TestClient:
    app_client.cookies.set(VIEW_COOKIE, mode)
    return app_client


def _all_urls(client: TestClient) -> list[str]:
    campaign = client.app.state.campaign  # type: ignore[attr-defined]
    urls = ["/", "/search?q=midcross", "/search?q=canary", "/mismatches", "/issues"]
    urls += [f"/browse/{t}" for t in ("npc", "location", "faction", "pc", "session")]
    urls += [f"/entry/{entry_id}" for entry_id in campaign.gm.index.docs]
    urls += [f"/place/{p.owner_id}/{p.slug}" for p in campaign.gm.index.places.values()]
    urls += ["/asset/casfer-portrait"]
    return urls


def test_every_gm_page_renders(app_client: TestClient) -> None:
    client = _client(app_client, "gm")
    for url in _all_urls(client):
        response = client.get(url)
        assert response.status_code == 200, url


def test_player_crawl_never_shows_secrets(app_client: TestClient) -> None:
    client = _client(app_client, "player")
    seen: set[str] = set()
    queue = _all_urls(client)
    while queue:
        url = queue.pop()
        if url in seen:
            continue
        seen.add(url)
        response = client.get(url)
        assert response.status_code in (200, 404), url
        assert CANARY not in response.text, url
        for href in re.findall(r'href="(/[^"#]*)', response.text):
            if not href.startswith(("/static", "/view")):
                queue.append(href)
    assert len(seen) > 30


def test_player_gets_404_for_hidden_things(app_client: TestClient) -> None:
    client = _client(app_client, "player")
    for url in (
        "/entry/vhallion",
        "/entry/thorne",
        "/entry/aetheria",
        "/mismatches",
        "/issues",
        "/place/midcross/smugglers-den",
    ):
        response = client.get(url)
        assert response.status_code == 404, url
        assert "Lost in the mists" in response.text


def test_home_gm_panels(app_client: TestClient) -> None:
    html = _client(app_client, "gm").get("/").text
    for text in (
        "Last session",
        "Spotlight",
        "Focus this session",
        "Coming up this week",
        "Mill burns",
        "Date passed",
        "Scouts vanish",
        "Hanging threads",
        "Session prep",
        "Remind them the bridge is out",
        "/entry/midcross#prep-1",
        "1/3",
        "Thunderday, 10 Seedmoon 1500 AS",
    ):
        assert text in html, text


def test_home_player_shows_only_last_session(app_client: TestClient) -> None:
    html = _client(app_client, "player").get("/").text
    assert "The party reached" in html
    assert "Spotlight" not in html and "Session prep" not in html and "Hanging threads" not in html


def test_entry_page_gm(app_client: TestClient) -> None:
    html = _client(app_client, "gm").get("/entry/casfer-ferndance").text
    assert "Quick card" in html and "Party believes:" in html
    assert "warming +0.75" in html and "+2.50" in html
    assert "Mentioned in" in html and 'class="secret-section"' not in html  # class list order
    assert "secret-section" in html and "GM only" in html


def test_entry_page_player_overrides(app_client: TestClient) -> None:
    client = _client(app_client, "player")
    assert "Runs a consulting firm" in client.get("/entry/casfer-ferndance").text
    teeth = client.get("/entry/the-teeth").text
    assert "The Teeth" in teeth and "Faythe" not in teeth
    haldir = client.get("/entry/haldir").text
    assert "alive" in haldir and "dead" not in haldir


def test_live_search_partial(app_client: TestClient) -> None:
    client = _client(app_client, "gm")
    partial = client.get("/search?q=bad+jokes", headers={"HX-Request": "true"}).text
    assert "<html" not in partial and "Casfer Ferndance" in partial and "<mark>" in partial
    full = client.get("/search?q=bad+jokes").text
    assert "<html" in full


def test_search_suggestions_and_filter(app_client: TestClient) -> None:
    client = _client(app_client, "gm")
    assert "Did you mean" in client.get("/search?q=Casfre+Ferndnce").text
    filtered = client.get("/search?q=midcross&type=session").text
    assert "Session 1" in filtered and "Lord Burgenroot" not in filtered


def test_view_toggle_sets_cookie_and_blocks_open_redirect(app_client: TestClient) -> None:
    response = app_client.get("/view/player?next=/entry/midcross", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/entry/midcross"
    assert "chronicler_view=player" in response.headers["set-cookie"]
    evil = app_client.get("/view/gm?next=https://evil.example", follow_redirects=False)
    assert evil.headers["location"] == "/"
    evil2 = app_client.get("/view/gm?next=//evil.example", follow_redirects=False)
    assert evil2.headers["location"] == "/"


def test_unknown_routes_and_types(app_client: TestClient) -> None:
    client = _client(app_client, "gm")
    assert client.get("/entry/nobody").status_code == 404
    assert client.get("/browse/spaceship").status_code == 404
    assert client.get("/place/midcross/nowhere").status_code == 404
    assert client.get("/asset/midcross").status_code == 404
    assert client.get("/totally/unknown").status_code == 404


def test_asset_served(app_client: TestClient) -> None:
    response = _client(app_client, "gm").get("/asset/casfer-portrait")
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG")


def test_static_assets(app_client: TestClient) -> None:
    for path in (
        "/static/css/chronicler.css",
        "/static/js/htmx.min.js",
        "/static/js/prefs.js",
        "/static/fonts/opendyslexic-latin-400-normal.woff2",
    ):
        assert app_client.get(path).status_code == 200, path


def test_issues_and_mismatches_pages(app_client: TestClient) -> None:
    client = _client(app_client, "gm")
    assert "No problems found." in client.get("/issues").text
    mismatches = client.get("/mismatches").text
    assert "Haldir" in mismatches and "life_state" in mismatches


def test_reloads_when_files_change(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign({"npcs/a.md": entry("a", "npc", name="Alpha")})
    client = TestClient(create_app(campaign.root))
    assert "Alpha" in client.get("/entry/a").text
    path = Path(campaign.root) / "npcs" / "b.md"
    path.write_text(entry("b", "npc", name="Beta"), encoding="utf-8")
    assert client.get("/entry/b").status_code == 200


def test_missing_asset_file(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign({"assets/x.md": entry("x", "asset", file="gone.png")})
    client = TestClient(create_app(campaign.root))
    assert client.get("/asset/x").status_code == 404


def test_empty_campaign_home(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign({})
    html = TestClient(create_app(campaign.root)).get("/").text
    assert "No sessions recorded yet." in html and "No current in-world date yet." in html
