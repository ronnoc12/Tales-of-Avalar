"""Edit mode, quick edit, dialogs, and the editor APIs, through the web app (HTMX requests)."""

from __future__ import annotations

import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chronicler.web.app import VIEW_COOKIE, create_app
from tests.conftest import FIXTURE, WRITE, logged_in

CAS = "npcs/casfer-ferndance.md"
HX = {**WRITE, "HX-Request": "true"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURE, tmp_path / "campaign")
    return tmp_path / "campaign"


@pytest.fixture
def gm(root: Path) -> TestClient:
    return logged_in(create_app(root), "gm")


def read(root: Path, rel: str) -> str:
    return (root / rel).read_bytes().decode("utf-8")


class FormReader(HTMLParser):
    """What a browser would submit for one form (htmx json-enc): name -> value."""

    def __init__(self, form_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.form_id, self.depth, self.values = form_id, 0, {}
        self._textarea: str | None = None
        self._text: list[str] = []
        self._select: str | None = None
        self._first: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "form" and a.get("id") == self.form_id:
            self.depth = 1
            return
        if not self.depth or "name" not in a:
            if self.depth and tag == "option" and self._select is not None:
                self._option(a)
            return
        name = a["name"]
        if tag == "input":
            kind = a.get("type", "text")
            if kind in ("checkbox", "radio"):
                if "checked" in a:
                    self.values[name] = a.get("value", "on")
            else:
                self.values[name] = a.get("value", "")
        elif tag == "textarea":
            self._textarea, self._text = name, []
        elif tag == "select":
            self._select, self._first = name, None

    def _option(self, a: dict[str, str]) -> None:
        assert self._select is not None
        if self._first is None:
            self._first = a.get("value", "")
            self.values[self._select] = self._first
        if "selected" in a:
            self.values[self._select] = a.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.depth:
            self.depth = 0
        elif tag == "textarea" and self._textarea is not None:
            text = "".join(self._text)
            self.values[self._textarea] = (
                text[1:] if text.startswith("\n") else text
            )  # as browsers do
            self._textarea = None
        elif tag == "select":
            self._select = None

    def handle_data(self, data: str) -> None:
        if self._textarea is not None:
            self._text.append(data)


def editor_form(client: TestClient, entry_id: str) -> dict[str, Any]:
    response = client.get(f"/edit/{entry_id}")
    assert response.status_code == 200, entry_id
    reader = FormReader("editor-form")
    reader.feed(response.text)
    return dict(reader.values)


def quick_form(html: str) -> dict[str, Any]:
    reader = FormReader("quick")
    reader.feed(html.replace('class="quick-form"', 'id="quick" class="quick-form"', 1))
    return dict(reader.values)


# -- the form round trip ------------------------------------------------------------------------


def test_saving_an_untouched_form_never_changes_any_file(root: Path, gm: TestClient) -> None:
    campaign = gm.app.state.campaign  # type: ignore[attr-defined]
    for document in list(campaign.gm.index.docs.values()):
        before = read(root, document.rel_path)
        form = editor_form(gm, document.id)
        response = gm.post(f"/edit/{document.id}/save", json=form, headers=HX)
        assert response.status_code == 200, (document.id, response.text)
        assert "Saved to the page." in response.text, (document.id, response.text)
        assert read(root, document.rel_path) == before, document.id


def test_edit_page_shows_fields_text_previews_and_names(gm: TestClient) -> None:
    html = gm.get("/edit/casfer-ferndance").text
    for text in (
        'name="f:disposition"',
        'name="p:disposition"',
        'name="r:relationships.1"',
        'id="body-editor"',
        "CANARY-INLINE-PERSONALITY",  # GM preview and text
        'id="preview-player"',
        '<option value="[[Midcross]]">',
        "/static/js/editor.js",
    ):
        assert text in html, text
    player_preview = html.split('id="preview-player"', 1)[1].split("</div>", 1)[0]
    assert "CANARY" not in player_preview


def test_editing_needs_the_gm_view(gm: TestClient) -> None:
    gm.cookies.set(VIEW_COOKIE, "player")
    response = gm.get("/edit/casfer-ferndance", follow_redirects=False)
    assert response.status_code == 303 and "/view/gm" in response.headers["location"]
    gm.cookies.set(VIEW_COOKIE, "gm")
    assert gm.get("/edit/nobody").status_code == 404


# -- autosave, save, discard, leave -------------------------------------------------------------


def test_autosave_skips_unchanged_work(root: Path, gm: TestClient) -> None:
    form = editor_form(gm, "casfer-ferndance")
    assert gm.post("/edit/casfer-ferndance/draft", json=form, headers=HX).status_code == 204
    assert not (root / "_drafts" / "casfer-ferndance.md").exists()


def test_autosave_keeps_a_draft_and_reports_problems(root: Path, gm: TestClient) -> None:
    form = editor_form(gm, "casfer-ferndance")
    form["body"] = "## Notes\n{secret}never closed\n"
    form["f:disposition"] = "12"
    response = gm.post("/edit/casfer-ferndance/draft", json=form, headers=HX)
    assert response.status_code == 200
    html = response.text
    assert "Draft saved" in html and "block saving" in html
    assert 'data-issue-line="1"' in html and 'data-fix="close-section"' in html
    assert 'id="preview-gm"' in html and 'hx-swap-oob="true"' in html
    assert 'data-draft="1"' in html
    assert (root / "_drafts" / "casfer-ferndance.md").exists()
    again = gm.post("/edit/casfer-ferndance/draft", json=form, headers=HX)
    assert again.status_code == 204  # same work: nothing saved twice
    reopened = gm.get("/edit/casfer-ferndance").text
    assert "Draft from" in reopened and "never closed" in reopened


def test_save_writes_the_page_and_updates_the_base(root: Path, gm: TestClient) -> None:
    form = editor_form(gm, "casfer-ferndance")
    form["f:race"] = "Gnome"
    response = gm.post("/edit/casfer-ferndance/save", json=form, headers=HX)
    assert "Saved to the page." in response.text and 'id="base-hash"' in response.text
    assert "race: Gnome" in read(root, CAS)
    assert not (root / "_drafts" / "casfer-ferndance.md").exists()


def test_save_with_errors_keeps_the_draft(root: Path, gm: TestClient) -> None:
    before = read(root, CAS)
    form = editor_form(gm, "casfer-ferndance")
    form["f:location"] = "[[Nowhere]]"
    response = gm.post("/edit/casfer-ferndance/save", json=form, headers=HX)
    assert "Not saved" in response.text and "matches nothing" in response.text
    assert read(root, CAS) == before and (root / "_drafts" / "casfer-ferndance.md").exists()


def test_save_conflict_keeps_the_work_and_says_where(root: Path, gm: TestClient) -> None:
    form = editor_form(gm, "casfer-ferndance")
    (root / CAS).write_bytes((root / CAS).read_bytes() + b"\nEdited in VS Code\n")
    form["body"] = "My edits"
    response = gm.post("/edit/casfer-ferndance/save", json=form, headers=HX)
    assert response.headers["HX-Retarget"] == "#modal-root" and "<dialog" in response.text
    path = re.search(r"<code>(_drafts/conflicts/[^<]+)</code>", response.text)
    assert path and read(root, path.group(1)).endswith("My edits")
    assert "Edited in VS Code" in read(root, CAS)


def test_save_then_navigates(gm: TestClient) -> None:
    form = editor_form(gm, "casfer-ferndance")
    response = gm.post("/edit/casfer-ferndance/save?then=/entry/midcross", json=form, headers=HX)
    assert response.headers["HX-Redirect"] == "/entry/midcross"


def test_discard_removes_the_draft(root: Path, gm: TestClient) -> None:
    form = editor_form(gm, "casfer-ferndance")
    form["body"] = "draft"
    gm.post("/edit/casfer-ferndance/draft", json=form, headers=HX)
    response = gm.post("/edit/casfer-ferndance/discard", json={}, headers=HX)
    assert response.headers["HX-Redirect"] == "/edit/casfer-ferndance"
    assert not (root / "_drafts" / "casfer-ferndance.md").exists()


def test_leave_asks_only_when_something_is_unsaved(gm: TestClient) -> None:
    form = editor_form(gm, "casfer-ferndance")
    quiet = gm.post("/edit/casfer-ferndance/leave?to=/entry/midcross", json=form, headers=HX)
    assert quiet.headers["HX-Redirect"] == "/entry/midcross"
    evil = gm.post("/edit/casfer-ferndance/leave?to=https://evil.example", json=form, headers=HX)
    assert evil.headers["HX-Redirect"] == "/"
    form["body"] = "typed but not autosaved"
    asked = gm.post("/edit/casfer-ferndance/leave?to=/entry/midcross", json=form, headers=HX)
    assert "<dialog" in asked.text and "Keep in draft" in asked.text and "Save now" in asked.text
    kept = gm.post("/edit/casfer-ferndance/keep?then=/entry/midcross", json=form, headers=HX)
    assert kept.headers["HX-Redirect"] == "/entry/midcross"
    assert "typed but not autosaved" in gm.get("/edit/casfer-ferndance").text


# -- row tools ------------------------------------------------------------------------------------


def row_count(html: str, field: str) -> int:
    return len(re.findall(rf'name="r:{field}\.\d+"', html))


def test_rows_add_remove_and_move(gm: TestClient) -> None:
    form = editor_form(gm, "casfer-ferndance")
    added = gm.post(
        "/edit/casfer-ferndance/rows",
        json={**form, "op": "add", "field": "goals", "index": 0},
        headers=HX,
    ).text
    assert row_count(added, "goals") == 3 and 'class="ef ef-rows ef-top"' in added
    moved = gm.post(
        "/edit/casfer-ferndance/rows",
        json={**form, "op": "down", "field": "goals", "index": 0},
        headers=HX,
    ).text
    first = moved.index("CANARY-SECRET-GOAL")
    assert first < moved.index("Keep USE neutral")
    removed = gm.post(
        "/edit/casfer-ferndance/rows",
        json={**form, "op": "remove", "field": "goals", "index": 1},
        headers=HX,
    ).text
    assert row_count(removed, "goals") == 1 and "CANARY-SECRET-GOAL" not in removed
    bad = gm.post(
        "/edit/casfer-ferndance/rows", json={**form, "op": "add", "field": "race"}, headers=HX
    )
    assert bad.status_code == 400


def test_a_new_empty_row_survives_another_row_change(gm: TestClient) -> None:
    form = editor_form(gm, "casfer-ferndance")
    form["r:goals.2"] = "1"  # the browser shows a just-added, still empty row
    html = gm.post(
        "/edit/casfer-ferndance/rows", json={**form, "op": "add", "field": "goals"}, headers=HX
    ).text
    assert row_count(html, "goals") == 4


# -- rename, retire, delete ----------------------------------------------------------------------


def test_rename_flow(root: Path, gm: TestClient) -> None:
    form = editor_form(gm, "midcross")
    opened = gm.post("/edit/midcross/rename-dialog", json=form, headers=HX)
    assert opened.headers["HX-Retarget"] == "#modal-root" and "New name" in opened.text
    base = re.search(r'name="base_hash" value="([0-9a-f]+)"', opened.text)
    assert base
    preview = gm.post(
        "/edit/midcross/rename-preview",
        json={"base_hash": base.group(1), "name": "Midcross Bay", "mode": "hard"},
        headers=HX,
    ).text
    assert "changes these files" in preview and "npcs/casfer-ferndance.md" in preview
    done = gm.post(
        "/edit/midcross/rename",
        json={"base_hash": base.group(1), "name": "Midcross Bay", "mode": "hard"},
        headers=HX,
    )
    assert done.headers["HX-Redirect"] == "/edit/midcross"
    assert "[[Midcross Bay]]" in read(root, CAS)


def test_rename_and_retire_ask_to_save_unsaved_edits_first(gm: TestClient) -> None:
    form = editor_form(gm, "midcross")
    form["body"] = "unsaved"
    for action in ("rename-dialog", "retire-dialog"):
        response = gm.post(f"/edit/midcross/{action}", json=form, headers=HX)
        assert "Save your edits first" in response.text, action


def test_rename_preview_explains_refusals(gm: TestClient) -> None:
    form = editor_form(gm, "midcross")
    response = gm.post(
        "/edit/midcross/rename-preview",
        json={"base_hash": form["base_hash"], "name": "Midcross", "mode": "alias"},
        headers=HX,
    )
    assert "different name" in response.text


def test_retire_flow(root: Path, gm: TestClient) -> None:
    form = editor_form(gm, "cassie-ferndance")
    dialog = gm.post("/edit/cassie-ferndance/retire-dialog", json=form, headers=HX).text
    assert "Retire this entry?" in dialog
    done = gm.post(
        "/edit/cassie-ferndance/retire", json={"base_hash": form["base_hash"]}, headers=HX
    )
    assert done.headers["HX-Redirect"] == "/edit/cassie-ferndance"
    assert "status: retired" in read(root, "npcs/cassie-ferndance.md")
    stale = gm.post(
        "/edit/cassie-ferndance/retire", json={"base_hash": form["base_hash"]}, headers=HX
    )
    assert "changed on disk" in stale.text


def test_delete_flow(root: Path, gm: TestClient) -> None:
    dialog = gm.get("/edit/cassie-ferndance/delete-dialog").text
    assert "Casfer Ferndance" in dialog and "Delete for good" in dialog
    base = re.search(r'name="base_hash" value="([0-9a-f]+)"', dialog)
    assert base
    done = gm.post("/edit/cassie-ferndance/delete", json={"base_hash": base.group(1)}, headers=HX)
    assert done.headers["HX-Redirect"] == "/browse/npc"
    assert not (root / "npcs" / "cassie-ferndance.md").exists()
    assert gm.get("/edit/nobody/delete-dialog").status_code == 404


# -- picker and toolbar ----------------------------------------------------------------------------


def test_picker_dialog_and_results(gm: TestClient) -> None:
    assert "Revealed in which session?" in gm.get("/edit/pick/reveal").text
    sessions = gm.get("/edit/pick/reveal/results?q=session").text
    assert 'data-pick="[[Session 1]]"' in sessions and "Session 1 ›" in sessions
    images = gm.get("/edit/pick/image/results").text
    assert 'data-pick="casfer-portrait"' in images and 'data-kind="image"' in images
    assert "Nothing matches." in gm.get("/edit/pick/link/results?q=zzzz").text
    assert gm.get("/edit/pick/nonsense").status_code == 404


def test_transform_api(gm: TestClient) -> None:
    text = "The lazy {secret}fox{/secret} jumps"
    edit = gm.post(
        "/api/transform",
        json={"op": "secret", "text": text, "start": 0, "end": len(text)},
        headers=WRITE,
    ).json()
    assert edit["insert"] == "{secret}The lazy fox jumps{/secret}"
    fix = gm.post(
        "/api/transform",
        json={"op": "close-section", "text": "{prep}open\nmore", "line": 0, "column": 0},
        headers=WRITE,
    ).json()
    assert fix["insert"] == "{/prep}"
    for op, extra in (
        ("prep", {}),
        ("unmark", {}),
        ("reveal", {"target": "[[Session 1]]"}),
        ("heading-secret", {}),
        ("link", {"target": "Midcross"}),
        ("embed", {"target": "map"}),
        ("close-paragraph", {}),
        ("remove-tag", {}),
    ):
        response = gm.post("/api/transform", json={"op": op, "text": "x", **extra}, headers=WRITE)
        assert response.status_code == 200, op


# -- quick edit -----------------------------------------------------------------------------------


def test_quick_edit_one_field(root: Path, gm: TestClient) -> None:
    page = gm.get("/entry/casfer-ferndance").text
    assert 'hx-get="/edit/casfer-ferndance/field/disposition"' in page
    fragment = gm.get("/edit/casfer-ferndance/field/disposition").text
    form = quick_form(fragment)
    assert form["f:disposition"] == "2.50"
    form["f:disposition"] = "1.5"
    response = gm.post("/edit/casfer-ferndance/field/disposition", json=form, headers=HX)
    assert response.status_code == 204 and response.headers["HX-Refresh"] == "true"
    assert "disposition: 1.50" in read(root, CAS)


def test_quick_edit_errors_and_conflicts_stay_in_the_fragment(root: Path, gm: TestClient) -> None:
    form = quick_form(gm.get("/edit/casfer-ferndance/field/disposition").text)
    form["f:disposition"] = "99"
    bad = gm.post("/edit/casfer-ferndance/field/disposition", json=form, headers=HX).text
    assert "between" in bad and 'class="quick-form"' in bad
    (root / CAS).write_bytes((root / CAS).read_bytes() + b"\nx\n")
    form["f:disposition"] = "1"
    stale = gm.post("/edit/casfer-ferndance/field/disposition", json=form, headers=HX).text
    assert "changed on disk" in stale


def test_quick_edit_only_for_simple_fields(gm: TestClient) -> None:
    assert gm.get("/edit/casfer-ferndance/field/relationships").status_code == 404
    assert gm.get("/edit/nobody/field/race").status_code == 404
    assert gm.post("/edit/casfer-ferndance/field/goals", json={}, headers=HX).status_code == 404


def test_no_edit_tools_in_the_player_view(gm: TestClient) -> None:
    gm.cookies.set(VIEW_COOKIE, "player")
    page = gm.get("/entry/casfer-ferndance").text
    assert "/edit/" not in page and "quick-edit" not in page
    gm.cookies.set(VIEW_COOKIE, "gm")


# -- new entry ------------------------------------------------------------------------------------


def test_new_entry(root: Path, gm: TestClient) -> None:
    assert "New entry" in gm.get("/new?type=lore").text
    hint = gm.get("/new/id-hint?name=Mira+Vale").text
    assert "mira-vale" in hint
    hidden = gm.get("/new/id-hint?name=Vhallion&hidden_name=1&party_name=The+Voice").text
    assert "the-voice" in hidden and "vhallion" not in hidden
    assert "made from the name" in gm.get("/new/id-hint").text
    response = gm.post(
        "/new",
        json={
            "entry_type": "npc",
            "name": "Vhallion Two",
            "hidden_name": "1",
            "party_name": "The Voice",
        },
        headers=HX,
    )
    assert response.headers["HX-Redirect"] == "/edit/the-voice"
    created = read(root, "npcs/the-voice.md")
    assert "name: Vhallion Two" in created and "party_view:" in created and "The Voice" in created
    duplicate = gm.post(
        "/new", json={"entry_type": "npc", "name": "Again", "entry_id": "the-voice"}, headers=HX
    )
    assert "already used" in duplicate.text


def test_home_lists_unsaved_drafts(gm: TestClient) -> None:
    form = editor_form(gm, "casfer-ferndance")
    form["body"] = "draft"
    gm.post("/edit/casfer-ferndance/draft", json=form, headers=HX)
    home = gm.get("/").text
    assert "Unsaved drafts" in home and 'href="/edit/casfer-ferndance"' in home
    assert "badge-draft" in gm.get("/entry/casfer-ferndance").text


# -- the JSON changes API -----------------------------------------------------------------------


def test_changes_api(root: Path, gm: TestClient) -> None:
    campaign = gm.app.state.campaign  # type: ignore[attr-defined]
    base = gm.app.state.changes.current_hash("casfer-ferndance")  # type: ignore[attr-defined]
    change = {"op": "update", "entry_id": "casfer-ferndance", "base_hash": base}
    dry = gm.post(
        "/api/changes", json={**change, "fields": {"race": "Elf"}, "dry_run": True}, headers=WRITE
    )
    assert dry.status_code == 200 and dry.json()["ok"] and not dry.json()["applied"]
    bad = gm.post("/api/changes", json={**change, "fields": {"disposition": 50}}, headers=WRITE)
    assert bad.status_code == 422 and bad.json()["errors"][0]["code"] == "schema"
    ok = gm.post("/api/changes", json={**change, "fields": {"race": "Elf"}}, headers=WRITE)
    assert ok.status_code == 200 and ok.json()["applied"] and "race: Elf" in read(root, CAS)
    stale = gm.post("/api/changes", json={**change, "fields": {"race": "Orc"}}, headers=WRITE)
    assert stale.status_code == 409
    nonsense = gm.post(
        "/api/changes", json={"op": "update", "entry_id": "nobody", "base_hash": "x"}, headers=WRITE
    )
    assert nonsense.status_code == 400
    assert campaign.gm.index.docs["casfer-ferndance"].meta["race"] == "Elf"
