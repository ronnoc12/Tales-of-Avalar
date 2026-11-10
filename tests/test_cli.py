from __future__ import annotations

from pathlib import Path

import pytest

from chronicler.cli import build_parser, main
from tests.conftest import FIXTURE, MakeCampaign, entry


def test_validate_clean(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(FIXTURE)]) == 0
    assert "0 error(s), 0 warning(s)" in capsys.readouterr().out


def test_validate_reports_errors(
    make_campaign: MakeCampaign, capsys: pytest.CaptureFixture[str]
) -> None:
    campaign = make_campaign({"npcs/a.md": entry("a", "npc", "see [[Nobody]]", favourite="pie")})
    assert main(["validate", str(campaign.root)]) == 1
    out = capsys.readouterr().out
    assert "broken-link" in out and "unknown-field" in out
    assert main(["validate", str(campaign.root), "--errors-only"]) == 1
    assert "unknown-field" not in capsys.readouterr().out


def test_missing_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = str(tmp_path / "nope")
    assert main(["validate", missing]) == 2
    assert main(["review", missing]) == 2
    assert main(["serve", missing]) == 2
    assert "not found" in capsys.readouterr().err


def test_review_single_entry(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["review", str(FIXTURE), "--entry", "Cas"]) == 0
    out = capsys.readouterr().out
    assert "Casfer Ferndance (casfer-ferndance)" in out and "Linked from:" in out


def test_review_unknown_entry_suggests(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["review", str(FIXTURE), "--entry", "Casfre Ferndance"]) == 2
    assert "Did you mean: Casfer Ferndance" in capsys.readouterr().err


def test_review_without_git(
    make_campaign: MakeCampaign, capsys: pytest.CaptureFixture[str]
) -> None:
    campaign = make_campaign({"npcs/a.md": entry("a", "npc")})
    assert main(["review", str(campaign.root)]) == 2
    assert "error:" in capsys.readouterr().err


def test_review_entry_with_no_references(
    make_campaign: MakeCampaign, capsys: pytest.CaptureFixture[str]
) -> None:
    campaign = make_campaign({"npcs/a.md": entry("a", "npc")})
    assert main(["review", str(campaign.root), "--entry", "a"]) == 0
    assert "Nothing else refers to it." in capsys.readouterr().out


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["serve"])
    assert (args.campaign, args.host, args.port) == ("campaign", "127.0.0.1", 8000)


def test_review_lists_status_changes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    git("config", "commit.gpgsign", "false")
    (tmp_path / "lore").mkdir()
    (tmp_path / "npcs").mkdir()
    (tmp_path / "lore" / "old.md").write_text(entry("old", "lore"), encoding="utf-8")
    (tmp_path / "npcs" / "a.md").write_text(entry("a", "npc", "Visit [[old]]."), encoding="utf-8")
    assert main(["review", str(tmp_path)]) == 0
    assert "new -> canon" in capsys.readouterr().out
    git("add", ".")
    git("commit", "-q", "-m", "init")
    assert main(["review", str(tmp_path)]) == 0
    assert "No status changes" in capsys.readouterr().out
    (tmp_path / "lore" / "old.md").write_text(
        entry("old", "lore", status="retired"), encoding="utf-8"
    )
    assert main(["review", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "canon -> retired" in out and "Canon entries still linking to it" in out


def test_serve_runs_uvicorn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uvicorn

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    assert main(["serve", str(FIXTURE), "--port", "8123"]) == 0
    assert calls == [{"host": "127.0.0.1", "port": 8123, "log_level": "warning"}]
    assert "http://127.0.0.1:8123" in capsys.readouterr().out
