from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chronicler.campaign import Campaign
from chronicler.review import ReviewError, review_entry, status_changes
from tests.conftest import entry


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    campaign = tmp_path / "campaign"
    files = {
        "lore/aetheria.md": entry("aetheria", "lore", "An elven city."),
        "lore/aetheria-v2.md": entry(
            "aetheria-v2", "lore", status="alternate", alternate_of='"[[aetheria]]"'
        ),
        "locations/midcross.md": entry("midcross", "location", "Trade with [[aetheria]]."),
        "npcs/bard.md": entry("bard", "npc", "Sings of Aetheria nightly.", status="draft"),
    }
    for rel, text in files.items():
        path = campaign / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return campaign


def test_no_changes(repo: Path) -> None:
    assert status_changes(Campaign(repo)) == []


def test_detects_status_change_and_new_entries(repo: Path) -> None:
    path = repo / "lore" / "aetheria.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: lore", "type: lore\nstatus: retired"),
        encoding="utf-8",
    )
    (repo / "npcs" / "new.md").write_text(entry("newbie", "npc"), encoding="utf-8")
    (repo / "npcs" / "bard.md").write_text(
        entry("bard", "npc", "edited", status="draft"), encoding="utf-8"
    )
    changes = {(c.entry_id, c.old, c.new) for c in status_changes(Campaign(repo))}
    assert changes == {("aetheria", "canon", "retired"), ("newbie", None, "canon")}


def test_review_report(repo: Path) -> None:
    path = repo / "lore" / "aetheria.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: lore", "type: lore\nstatus: retired"),
        encoding="utf-8",
    )
    report = review_entry(Campaign(repo), "aetheria")
    assert report.status == "retired" and not report.empty
    assert {r.source_id for r in report.backlinks} == {"midcross", "aetheria-v2"}
    assert [r.source_id for r in report.canon_linkers] == ["midcross"]  # the alternate isn't canon
    assert [m.source_id for m in report.mentions] == ["bard"]
    assert report.alternates == ["aetheria-v2"]


def test_alternate_sees_its_siblings(repo: Path) -> None:
    report = review_entry(Campaign(repo), "aetheria-v2")
    assert report.alternates == ["aetheria"]
    assert report.canon_linkers == []


def test_not_a_git_repo(tmp_path: Path) -> None:
    (tmp_path / "npcs").mkdir()
    (tmp_path / "npcs" / "a.md").write_text(entry("a", "npc"), encoding="utf-8")
    with pytest.raises(ReviewError):
        status_changes(Campaign(tmp_path))


def test_repo_without_commits(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "npcs").mkdir()
    (tmp_path / "npcs" / "a.md").write_text(entry("a", "npc"), encoding="utf-8")
    changes = status_changes(Campaign(tmp_path))
    assert [(c.entry_id, c.old) for c in changes] == [("a", None)]
