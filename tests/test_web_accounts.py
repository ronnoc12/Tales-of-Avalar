"""Login, setup, roles, the Users page, and the guard against forged requests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chronicler.web.app import VIEW_COOKIE, create_app
from tests.conftest import CANARY, FIXTURE, PASSWORD, WRITE, MakeCampaign, entry, logged_in

HX = {**WRITE, "HX-Request": "true"}


@pytest.fixture
def app(tmp_path: Path) -> object:
    return create_app(FIXTURE, data_dir=tmp_path / "data")


# -- first run, login, logout ---------------------------------------------------------------------


def test_first_run_goes_to_setup(app: object) -> None:
    client = TestClient(app)  # type: ignore[arg-type]
    assert client.get("/", follow_redirects=False).headers["location"] == "/setup"
    assert client.get("/login", follow_redirects=False).headers["location"] == "/setup"
    assert "Create the GM account" in client.get("/setup").text
    mismatch = client.post(
        "/setup", json={"username": "gm", "password": PASSWORD, "confirm": "x"}, headers=HX
    )
    assert "do not match" in mismatch.text
    short = client.post(
        "/setup", json={"username": "gm", "password": "short", "confirm": "short"}, headers=HX
    )
    assert "at least 8" in short.text
    done = client.post(
        "/setup", json={"username": "gm", "password": PASSWORD, "confirm": PASSWORD}, headers=HX
    )
    assert done.headers["HX-Redirect"] == "/" and "chronicler_session" in done.headers["set-cookie"]
    assert (
        "HttpOnly" in done.headers["set-cookie"] and "SameSite=strict" in done.headers["set-cookie"]
    )
    assert client.get("/").status_code == 200
    assert client.get("/setup", follow_redirects=False).headers["location"] == "/login"
    again = client.post(
        "/setup", json={"username": "x", "password": PASSWORD, "confirm": PASSWORD}, headers=HX
    )
    assert again.status_code == 403


def test_logged_out_requests(app: object) -> None:
    logged_in(app, "gm")  # an account exists  # type: ignore[arg-type]
    client = TestClient(app)  # type: ignore[arg-type]
    page = client.get("/entry/midcross?x=1", follow_redirects=False)
    assert (
        page.status_code == 303
        and page.headers["location"] == "/login?next=/entry/midcross%3Fx%3D1"
    )
    api = client.post("/api/transform", json={"op": "secret", "text": ""}, headers=WRITE)
    assert api.status_code == 401
    part = client.get(
        "/edit/midcross/field/race",
        headers={"HX-Request": "true", "HX-Current-URL": "http://t/entry/midcross"},
    )
    assert part.status_code == 204 and part.headers["HX-Redirect"] == "/login?next=/entry/midcross"
    missing = client.get("/no/such/page", follow_redirects=False)
    assert missing.headers["location"].startswith("/login")
    assert "Log in" in client.get("/login?next=/entry/midcross").text


def test_login_logout_and_safe_next(app: object) -> None:
    gm = logged_in(app, "gm")  # type: ignore[arg-type]
    assert (
        gm.get("/login?next=/entry/midcross", follow_redirects=False).headers["location"]
        == "/entry/midcross"
    )
    fresh = TestClient(app)  # type: ignore[arg-type]
    wrong = fresh.post("/login", json={"username": "test-gm", "password": "nope nope"}, headers=HX)
    assert "Wrong name or password." in wrong.text
    evil = fresh.post(
        "/login?next=//evil.example", json={"username": "test-gm", "password": PASSWORD}, headers=HX
    )
    assert evil.headers["HX-Redirect"] == "/"
    out = gm.post("/logout", json={}, headers=HX)
    assert out.headers["HX-Redirect"] == "/login"
    assert gm.get("/", follow_redirects=False).status_code == 303


def test_repeated_wrong_passwords_slow_down(app: object) -> None:
    logged_in(app, "gm")  # type: ignore[arg-type]
    client = TestClient(app)  # type: ignore[arg-type]
    for _ in range(5):
        client.post("/login", json={"username": "test-gm", "password": "nope nope"}, headers=HX)
    blocked = client.post("/login", json={"username": "test-gm", "password": PASSWORD}, headers=HX)
    assert "Too many tries" in blocked.text


# -- roles ----------------------------------------------------------------------------------------


def test_a_player_never_sees_secrets_or_edit_tools(app: object) -> None:
    player = logged_in(app, "player")  # type: ignore[arg-type]
    player.cookies.set(VIEW_COOKIE, "gm")  # a Player cannot switch to the GM view
    seen: set[str] = set()
    queue = ["/", "/search?q=canary", "/browse/npc", "/entry/casfer-ferndance"]
    while queue:
        url = queue.pop()
        if url in seen:
            continue
        seen.add(url)
        response = player.get(url)
        assert response.status_code in (200, 404), url
        assert CANARY not in response.text, url
        assert "/edit/" not in response.text and "New entry" not in response.text, url
        for href in re.findall(r'href="(/[^"#]*)', response.text):
            if not href.startswith(("/static", "/view")):
                queue.append(href)
    assert len(seen) > 20
    assert "Player view: secrets are hidden." in player.get("/").text


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/edit/casfer-ferndance"),
        ("get", "/edit/casfer-ferndance/field/race"),
        ("get", "/users"),
        ("get", "/new"),
        ("post", "/api/changes"),
        ("post", "/api/transform"),
        ("post", "/edit/casfer-ferndance/save"),
        ("post", "/users"),
    ],
)
def test_players_are_refused_every_gm_route(app: object, method: str, url: str) -> None:
    player = logged_in(app, "player")  # type: ignore[arg-type]
    response = getattr(player, method)(
        url, **({"json": {}, "headers": WRITE} if method == "post" else {})
    )
    assert response.status_code == 403, url


# -- forged requests ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},  # no X-Chronicler header
        {"X-Chronicler": "1", "Content-Type": "text/plain"},
        {"X-Chronicler": "1", "Origin": "https://evil.example"},
        {"X-Chronicler": "1", "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_forged_writes_are_refused(app: object, headers: dict[str, str]) -> None:
    gm = logged_in(app, "gm")  # type: ignore[arg-type]
    if headers.get("Content-Type") == "text/plain":
        response = gm.post("/api/transform", content='{"op":"secret","text":""}', headers=headers)
        assert response.status_code == 415
        return
    response = gm.post("/api/transform", json={"op": "secret", "text": ""}, headers=headers)
    assert response.status_code == 403


def test_same_site_origin_is_accepted(app: object) -> None:
    gm = logged_in(app, "gm")  # type: ignore[arg-type]
    response = gm.post(
        "/api/transform",
        json={"op": "secret", "text": "x", "end": 1},
        headers={**WRITE, "Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200


# -- the Users page -------------------------------------------------------------------------------


def test_users_page(app: object) -> None:
    gm = logged_in(app, "gm")  # type: ignore[arg-type]
    assert "Add an account" in gm.get("/users").text
    added = gm.post(
        "/users", json={"username": "jo", "password": PASSWORD, "role": "player"}, headers=HX
    )
    assert added.headers["HX-Redirect"] == "/users"
    assert (
        "already has"
        in gm.post("/users", json={"username": "JO", "password": PASSWORD}, headers=HX).text
    )
    changed = gm.post("/users/jo/password", json={"password": "a new password"}, headers=HX)
    assert "Password changed" in changed.text
    assert "at least 8" in gm.post("/users/jo/password", json={"password": "x"}, headers=HX).text
    jo = TestClient(app)  # type: ignore[arg-type]
    login = jo.post("/login", json={"username": "jo", "password": "a new password"}, headers=HX)
    assert login.status_code == 204
    assert "your own" in gm.post("/users/test-gm/remove", json={}, headers=HX).text
    assert gm.post("/users/jo/remove", json={}, headers=HX).headers["HX-Redirect"] == "/users"
    assert "no account" in gm.post("/users/jo/remove", json={}, headers=HX).text


def test_default_data_folder_is_inside_the_campaign(make_campaign: MakeCampaign) -> None:
    campaign = make_campaign({"npcs/a.md": entry("a", "npc")})
    logged_in(create_app(campaign.root))
    assert (Path(campaign.root) / "_data" / "chronicler.db").is_file()
