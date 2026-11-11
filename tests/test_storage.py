"""Storage writes: safe paths, exact bytes, atomic swaps."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chronicler import storage
from chronicler.storage import CampaignRepository, StorageError, atomic_write


@pytest.fixture
def repo(tmp_path: Path) -> CampaignRepository:
    (tmp_path / "npcs").mkdir()
    (tmp_path / "_templates").mkdir()
    (tmp_path / "_templates" / "npc.md").write_bytes(b"---\ntype: npc\n---\n")
    return CampaignRepository(tmp_path)


@pytest.mark.parametrize(
    "rel",
    ["../outside.md", "npcs/a.txt", "_templates/npc.md", "_drafts/a.md", ".git/a.md", "."],
)
def test_refuses_paths_that_are_not_entries(repo: CampaignRepository, rel: str) -> None:
    with pytest.raises(StorageError):
        repo.entry_path(rel)


def test_write_then_read_keeps_exact_text(repo: CampaignRepository) -> None:
    text = "---\r\nid: a\r\n---\r\nBody ✓\r\n"
    repo.write_entry("npcs/a.md", text)
    assert repo.read_entry("npcs/a.md") == text
    assert not [p for p in (repo.root / "npcs").iterdir() if p.suffix == ".tmp"]


def test_hash_tracks_content(repo: CampaignRepository) -> None:
    assert repo.entry_hash("npcs/a.md") is None
    repo.write_entry("npcs/a.md", "one")
    first = repo.entry_hash("npcs/a.md")
    repo.write_entry("npcs/a.md", "two")
    assert first and repo.entry_hash("npcs/a.md") not in (None, first)


def test_new_folders_are_created(repo: CampaignRepository) -> None:
    repo.write_entry("lore/new.md", "x")
    assert repo.read_entry("lore/new.md") == "x"


def test_delete(repo: CampaignRepository) -> None:
    repo.write_entry("npcs/a.md", "x")
    repo.delete_entry("npcs/a.md")
    assert not (repo.root / "npcs" / "a.md").exists()
    with pytest.raises(StorageError):
        repo.delete_entry("npcs/a.md")


def test_templates_and_private_dirs(repo: CampaignRepository) -> None:
    assert repo.template_text("npc") == "---\ntype: npc\n---\n"
    assert repo.template_text("spaceship") is None
    assert repo.private_dir("_drafts") == repo.root / "_drafts"
    with pytest.raises(StorageError):
        repo.private_dir("npcs")


def test_failed_swap_cleans_up_and_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(src: str, dst: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(storage.os, "replace", refuse)
    monkeypatch.setattr(storage.time, "sleep", lambda _s: None)
    with pytest.raises(StorageError, match="locked"):
        atomic_write(tmp_path / "a.md", b"x")
    assert os.listdir(tmp_path) == []


def test_swap_retries_a_briefly_locked_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = os.replace
    calls: list[int] = []

    def flaky(src: str, dst: Path) -> None:
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("busy")
        real_replace(src, dst)

    monkeypatch.setattr(storage.os, "replace", flaky)
    monkeypatch.setattr(storage.time, "sleep", lambda _s: None)
    atomic_write(tmp_path / "a.md", b"x")
    assert (tmp_path / "a.md").read_bytes() == b"x" and len(calls) == 3
