"""The changes service: plan, check against the whole world, then write (or keep a draft)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from chronicler.campaign import Campaign
from chronicler.changes import (
    Change,
    ChangeError,
    ChangeService,
    apply_fields,
    merge_meta,
    normalize_numbers,
    prune,
)
from chronicler.document import parse_document
from chronicler.serialize import EntryText
from chronicler.storage import StorageError
from tests.conftest import FIXTURE

CAS = "npcs/casfer-ferndance.md"


@pytest.fixture
def service(tmp_path: Path) -> ChangeService:
    root = tmp_path / "campaign"
    shutil.copytree(FIXTURE, root)
    return ChangeService(Campaign(root))


def read(service: ChangeService, rel: str) -> str:
    return service.repository.read_entry(rel)


def base(service: ChangeService, entry_id: str = "casfer-ferndance") -> str | None:
    return service.current_hash(entry_id)


def full_edit(service: ChangeService, entry_id: str = "casfer-ferndance") -> Change:
    state = service.editor_state(entry_id)
    return Change("update", entry_id, state.base_hash, meta=dict(state.meta), body=state.body)


def autosave(service: ChangeService, change: Change, body: str) -> None:
    service.save_draft(
        change.entry_id, meta=dict(change.meta or {}), body=body, base_hash=change.base_hash
    )


# -- helpers -----------------------------------------------------------------------------


def test_prune_and_merge_helpers() -> None:
    assert prune({"a": "", "b": [{}, {"c": None}, 1], "d": {"e": []}, "f": 0}) == {
        "b": [1],
        "f": 0,
    }
    current = {"id": "x", "aliases": [], "race": "Elf", "age": 30}
    merged = merge_meta(current, {"id": "x", "aliases": [], "race": "", "age": 31})
    assert merged == {"id": "x", "aliases": [], "age": 31}  # untouched empty kept; cleared dropped
    placeholders = {"motto": "", "first_met": {"location": "", "session": ""}, "map_pin": None}
    from_form = {"motto": None, "first_met": {"location": None, "session": None}, "map_pin": {}}
    assert merge_meta(placeholders, from_form) == placeholders  # a form shows "" as empty
    assert apply_fields(current, {"race": None, "role": "Spy"}) == {
        "id": "x",
        "aliases": [],
        "age": 30,
        "role": "Spy",
    }


def test_normalize_numbers_formats_scores() -> None:
    meta = {
        "id": "x",
        "type": "npc",
        "name": "X",
        "disposition": 2.5,
        "factions": [{"faction": "[[F]]", "standing": "1"}],
    }
    normalized = normalize_numbers(meta)
    assert str(normalized["disposition"]) == "2.50"
    assert str(normalized["factions"][0]["standing"]) == "1.00"
    bad = {"id": "x", "type": "npc", "name": "X", "disposition": 99}
    assert normalize_numbers(bad) is bad
    assert normalize_numbers({"type": "spaceship"}) == {"type": "spaceship"}


# -- updates -------------------------------------------------------------------------------


def test_quick_edit_changes_one_line(service: ChangeService) -> None:
    before = read(service, CAS)
    outcome = service.apply(
        Change("update", "casfer-ferndance", base(service), fields={"disposition": 1.5})
    )
    assert outcome.ok and outcome.applied and outcome.new_hash == base(service)
    after = read(service, CAS)
    diff = [(a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b]
    assert diff == [("disposition: 2.50", "disposition: 1.50")]
    assert service.campaign.gm.index.docs["casfer-ferndance"].meta["disposition"] == 1.5


def test_saving_without_changes_leaves_the_file_identical(service: ChangeService) -> None:
    before = read(service, CAS)
    assert service.apply(full_edit(service)).applied
    assert read(service, CAS) == before


def test_edit_mode_save_replaces_body_and_clears_draft(service: ChangeService) -> None:
    change = full_edit(service)
    autosave(service, change, "draft")
    change.body = "## New\nWritten in the browser.\n"
    assert service.apply(change).applied
    text = read(service, CAS).replace("\r\n", "\n")
    assert text.endswith("---\n## New\nWritten in the browser.\n")
    assert service.drafts.get("casfer-ferndance") is None


def test_errors_block_and_nothing_is_written(service: ChangeService) -> None:
    before = read(service, CAS)
    outcome = service.apply(
        Change("update", "casfer-ferndance", base(service), fields={"disposition": 11})
    )
    assert not outcome.ok and not outcome.applied
    assert [e.code for e in outcome.errors] == ["schema"]
    assert read(service, CAS) == before


def test_dry_run_reports_but_never_writes(service: ChangeService) -> None:
    before = read(service, CAS)
    broken = Change("update", "casfer-ferndance", base(service), fields={"location": "[[Nowhere]]"})
    outcome = service.apply(broken, dry_run=True)
    assert outcome.ok is False and [e.code for e in outcome.errors] == ["broken-link"]
    fine = Change("update", "casfer-ferndance", base(service), fields={"race": "Gnome"})
    outcome = service.apply(fine, dry_run=True)
    assert outcome.ok and not outcome.applied and read(service, CAS) == before


def test_warnings_never_block(service: ChangeService) -> None:
    outcome = service.apply(
        Change("update", "casfer-ferndance", base(service), fields={"mood": "grim"})
    )
    assert outcome.applied and [w.code for w in outcome.warnings] == ["unknown-field"]


@pytest.mark.parametrize(
    ("fields", "message"),
    [({"id": "someone-else"}, "id never changes"), ({"type": "location"}, "type cannot change")],
)
def test_id_and_type_are_fixed(
    service: ChangeService, fields: dict[str, str], message: str
) -> None:
    with pytest.raises(ChangeError, match=message):
        service.apply(Change("update", "casfer-ferndance", base(service), fields=fields))


def test_requests_that_make_no_sense(service: ChangeService) -> None:
    with pytest.raises(ChangeError, match="base_hash"):
        service.apply(Change("update", "casfer-ferndance", None, fields={"race": "Elf"}))
    with pytest.raises(ChangeError, match="no entry"):
        service.apply(Change("update", "nobody", "x", fields={"race": "Elf"}))


# -- conflicts -------------------------------------------------------------------------------


def outside_edit(service: ChangeService) -> None:
    path = service.repository.entry_path(CAS)
    path.write_bytes(path.read_bytes() + b"\nEdited in VS Code.\n")


def test_edit_mode_conflict_keeps_work_as_a_conflict_copy(service: ChangeService) -> None:
    change = full_edit(service)
    autosave(service, change, "mine")
    outside_edit(service)
    change.body = "My edits.\n"
    assert service.apply(change, dry_run=True).conflict  # dry run: nothing moves
    assert service.drafts.get("casfer-ferndance") is not None
    outcome = service.apply(change)
    assert outcome.conflict and not outcome.applied and outcome.conflict_path
    kept = (service.repository.root / outcome.conflict_path).read_text(encoding="utf-8")
    assert kept.endswith("My edits.\n")
    assert "Edited in VS Code." in read(service, CAS)
    assert service.drafts.get("casfer-ferndance") is None  # reload starts from the file


def test_quick_edit_conflict_never_touches_the_draft(service: ChangeService) -> None:
    change = full_edit(service)
    autosave(service, change, "mine")
    outside_edit(service)
    quick = Change("update", "casfer-ferndance", change.base_hash, fields={"race": "Elf"})
    outcome = service.apply(quick)
    assert outcome.conflict and outcome.conflict_path is None
    assert service.drafts.get("casfer-ferndance") is not None


# -- drafts ----------------------------------------------------------------------------------


def test_autosave_stores_draft_and_reports_issues(service: ChangeService) -> None:
    state = service.editor_state("casfer-ferndance")
    assert not state.from_draft and not state.stale
    meta = dict(state.meta, disposition=15)
    check = service.save_draft(
        "casfer-ferndance", meta=meta, body="{secret}unclosed", base_hash=state.base_hash
    )
    assert check.info.base_hash == state.base_hash and not check.stale
    assert {e.code for e in check.errors} == {"schema", "unclosed-span"} and check.warnings == []
    unclosed = next(e for e in check.errors if e.code == "unclosed-span")
    assert unclosed.line == check.body_line  # the body's first line
    reopened = service.editor_state("casfer-ferndance")
    assert reopened.from_draft and reopened.body == "{secret}unclosed"
    assert reopened.meta["disposition"] == 15 and reopened.saved_at
    outside_edit(service)
    assert service.editor_state("casfer-ferndance").stale


def test_quick_edit_updates_the_open_draft(service: ChangeService) -> None:
    change = full_edit(service)
    autosave(service, change, "draft body")
    quick = Change("update", "casfer-ferndance", change.base_hash, fields={"race": "Gnome"})
    service.apply(quick)
    reopened = service.editor_state("casfer-ferndance")
    assert reopened.meta["race"] == "Gnome" and reopened.body == "draft body"
    assert reopened.base_hash == base(service) and not reopened.stale


def test_preview_document(service: ChangeService) -> None:
    state = service.editor_state("casfer-ferndance")
    doc = service.draft_document("casfer-ferndance", meta=dict(state.meta), body="## Draft\nHi")
    assert [s.title for s in doc.sections] == ["Draft"]


def test_editor_state_body_matches_file(service: ChangeService) -> None:
    state = service.editor_state("casfer-ferndance")
    assert state.body == EntryText.parse(read(service, CAS)).body


# -- create, rename, retire, delete ------------------------------------------------------------


def test_create_from_template_keeps_its_guidance(service: ChangeService) -> None:
    template = "---\nid: new-npc   # never change\ntype: npc\nname: New NPC\nrace:\n---\n## Looks\n"
    service.repository.root.joinpath("_templates", "npc.md").write_text(template, encoding="utf-8")
    outcome = service.apply(Change("create", "mira-vale", entry_type="npc", name="Mira Vale"))
    assert outcome.applied
    text = read(service, "npcs/mira-vale.md").replace("\r\n", "\n")
    assert text.startswith("---\nid: mira-vale") and "# never change\n" in text
    assert "name: Mira Vale" in text
    assert text.endswith("## Looks\n")
    assert service.campaign.gm.index.get("mira-vale") is not None


def test_create_without_template(service: ChangeService) -> None:
    outcome = service.apply(Change("create", "old-road", entry_type="lore", name="The Old Road"))
    assert outcome.applied
    assert parse_document(read(service, "lore/old-road.md"), "x").meta == {
        "id": "old-road",
        "type": "lore",
        "name": "The Old Road",
    }


@pytest.mark.parametrize(
    ("entry_id", "entry_type", "name", "message"),
    [
        ("casfer-ferndance", "npc", "Dupe", "already used"),
        ("Bad Id", "npc", "X", "lowercase"),
        ("x", "spaceship", "X", "unknown entry type"),
        ("x", "npc", " ", "needs a name"),
    ],
)
def test_create_refusals(
    service: ChangeService, entry_id: str, entry_type: str, name: str, message: str
) -> None:
    with pytest.raises(ChangeError, match=message):
        service.apply(Change("create", entry_id, entry_type=entry_type, name=name))


def test_rename_keeping_an_alias(service: ChangeService) -> None:
    rename = Change("rename", "casfer-ferndance", base(service), name="Cas the Bold")
    outcome = service.apply(rename)
    assert outcome.applied and [f.rel_path for f in outcome.files] == [CAS]
    meta = service.campaign.gm.index.docs["casfer-ferndance"].meta
    assert meta["name"] == "Cas the Bold" and "Casfer Ferndance" in meta["aliases"]
    assert service.campaign.gm.index.resolve("Casfer Ferndance").entry_id == "casfer-ferndance"


def test_hard_rename_rewrites_links_everywhere(service: ChangeService) -> None:
    linking = [
        d.rel_path for d in service.campaign.documents if "[[Midcross" in read(service, d.rel_path)
    ]
    rename = Change(
        "rename", "midcross", base(service, "midcross"), name="Midcross Bay", rename_mode="hard"
    )
    outcome = service.apply(rename)
    assert outcome.ok and outcome.applied, outcome.errors
    changed = sorted(f.rel_path for f in outcome.files)
    assert changed == sorted({*linking, "locations/midcross.md"})
    for rel in linking:
        assert "[[Midcross]]" not in read(service, rel) and "[[Midcross#" not in read(service, rel)
    assert "[[Midcross Bay]]" in read(service, CAS)
    assert service.campaign.gm.index.docs["midcross"].name == "Midcross Bay"
    # "Midcross" still resolves, but only through the permanent id, never as a name.
    assert "Midcross" not in service.campaign.gm.index.docs["midcross"].meta.get("aliases", [])


def test_hard_rename_refuses_a_shared_name(service: ChangeService) -> None:
    service.apply(Change("create", "midcross-two", entry_type="lore", name="Midcross"))
    rename = Change("rename", "midcross", base(service, "midcross"), name="M", rename_mode="hard")
    with pytest.raises(ChangeError, match="alias instead"):
        service.apply(rename)


def test_rename_needs_a_new_name(service: ChangeService) -> None:
    with pytest.raises(ChangeError, match="different name"):
        service.apply(Change("rename", "midcross", base(service, "midcross"), name="Midcross"))


def test_retire_keeps_the_page_and_its_draft_stays_retired(service: ChangeService) -> None:
    change = full_edit(service, "cassie-ferndance")
    autosave(service, change, change.body or "")
    outcome = service.apply(Change("retire", "cassie-ferndance", change.base_hash))
    assert outcome.applied
    assert service.campaign.gm.index.docs["cassie-ferndance"].meta["status"] == "retired"
    # Elsewhere, canon pages now link to a retired entry whose name players will see as text.
    assert {w.code for w in outcome.warnings} == {"links-non-canon", "shows-hidden-name"}
    reopened = service.editor_state("cassie-ferndance")
    assert not reopened.stale and reopened.meta["status"] == "retired"  # saving it won't undo


def test_rename_carries_into_the_open_draft(service: ChangeService) -> None:
    change = full_edit(service, "midcross")
    autosave(service, change, "Ask at [[Midcross#Market]] and [[Midcross]].")
    rename = Change("rename", "midcross", change.base_hash, name="Midcross Bay", rename_mode="hard")
    assert service.apply(rename).applied
    draft = service.editor_state("midcross")
    assert draft.meta["name"] == "Midcross Bay" and not draft.stale
    assert draft.body == "Ask at [[Midcross Bay#Market]] and [[Midcross Bay]]."
    saved = Change("update", "midcross", draft.base_hash, meta=dict(draft.meta), body=draft.body)
    assert service.apply(saved).applied
    assert service.campaign.gm.index.docs["midcross"].name == "Midcross Bay"


def test_delete_preview_then_delete(service: ChangeService) -> None:
    change = full_edit(service, "cassie-ferndance")
    autosave(service, change, "x")
    preview = service.delete_preview("cassie-ferndance")
    assert "casfer-ferndance" in preview.backlinks
    assert preview.drafts == ["cassie-ferndance.md", "cassie-ferndance.json"]
    assert preview.uncommitted is None  # the temp folder is not a git repository
    outcome = service.apply(Change("delete", "cassie-ferndance", change.base_hash))
    assert outcome.applied and not outcome.errors
    assert {w.code for w in outcome.warnings} == {"broken-link"}  # elsewhere: warned, not blocked
    assert not service.repository.entry_path("npcs/cassie-ferndance.md").exists()
    assert service.drafts.files_for("cassie-ferndance") == []


def test_failed_batch_restores_written_files(
    service: ChangeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = {d.rel_path: read(service, d.rel_path) for d in service.campaign.documents}
    real_write = service.repository.write_entry
    calls: list[str] = []

    def flaky(rel: str, text: str) -> None:
        calls.append(rel)
        if len(calls) == 2:
            raise StorageError("disk full")
        real_write(rel, text)

    monkeypatch.setattr(service.repository, "write_entry", flaky)
    rename = Change("rename", "midcross", base(service, "midcross"), name="Bay", rename_mode="hard")
    with pytest.raises(StorageError):
        service.apply(rename)
    monkeypatch.setattr(service.repository, "write_entry", real_write)
    assert all(read(service, rel) == text for rel, text in before.items())
