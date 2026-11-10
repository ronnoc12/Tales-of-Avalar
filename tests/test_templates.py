"""Every blank template must validate cleanly once copied into a campaign."""

from __future__ import annotations

import shutil
from pathlib import Path

from chronicler.campaign import Campaign
from chronicler.document import parse_document
from chronicler.schema import TYPE_FOLDERS
from chronicler.validate import validate

TEMPLATES = Path(__file__).parent.parent / "campaign" / "_templates"


def test_one_template_per_type() -> None:
    types = {
        parse_document(p.read_text(encoding="utf-8"), p.name).type for p in TEMPLATES.glob("*.md")
    }
    assert types == set(TYPE_FOLDERS)


def test_templates_validate(tmp_path: Path) -> None:
    for template in TEMPLATES.glob("*.md"):
        entry_type = parse_document(template.read_text(encoding="utf-8"), template.name).type
        target = tmp_path / TYPE_FOLDERS[entry_type] / template.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(template, target)
    files = tmp_path / "assets" / "files"
    files.mkdir(parents=True)
    (files / "new-asset.png").write_bytes(b"")
    issues = validate(Campaign(tmp_path))
    assert [i.format() for i in issues] == []


def test_real_campaign_config_loads() -> None:
    campaign = Campaign(TEMPLATES.parent)
    assert campaign.config_issues == []
    assert campaign.config.name == "Tales of Avalar"
    assert campaign.calendar.format_year(-300) == "300 BS"
