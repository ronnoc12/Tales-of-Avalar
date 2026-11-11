"""Draft files: one per entry, conflict copies, and exact ownership."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronicler.drafts import DraftStore
from chronicler.storage import CampaignRepository, StorageError


@pytest.fixture
def store(tmp_path: Path) -> DraftStore:
    return DraftStore(CampaignRepository(tmp_path))


def test_save_get_list_discard(store: DraftStore) -> None:
    assert store.get("a") is None and store.list() == []
    info = store.save("a", "npcs/a.md", "---\nid: a\n---\n", "hash1")
    draft = store.get("a")
    assert draft is not None and draft.text == "---\nid: a\n---\n" and draft.info == info
    assert [d.entry_id for d in store.list()] == ["a"]
    assert store.discard("a") and not store.discard("a")
    assert store.get("a") is None


def test_ids_are_checked(store: DraftStore) -> None:
    with pytest.raises(StorageError):
        store.get("../escape")


def test_corrupt_sidecar_is_ignored(store: DraftStore) -> None:
    store.save("a", "npcs/a.md", "x", None)
    (store.root / "a.json").write_text("{not json", encoding="utf-8")
    assert store.get("a") is None and store.list() == []


def test_conflict_copies_never_collide_and_belong_to_exactly_one_entry(
    store: DraftStore,
) -> None:
    store.save("session", "sessions/session.md", "x", None)
    first = store.move_to_conflicts("session", "one")
    second = store.move_to_conflicts("session", "two")
    other = store.move_to_conflicts("session-2", "not mine")
    assert first != second and store.get("session") is None
    assert set(store.files_for("session")) == {first, second}
    assert store.files_for("session-2") == [other]
    assert store.delete_all("session") and other.exists()
