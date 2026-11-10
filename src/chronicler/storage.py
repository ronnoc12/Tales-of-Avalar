"""Storage: the single place that reads campaign files from disk.

Everything else works with :class:`Document` objects from here, so the storage backend
could later be swapped (for example, for a database) without touching other modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from chronicler.calendar import CalendarConfig, CalendarError
from chronicler.document import Document, DocumentIssue, parse_document

CAMPAIGN_FILE = "campaign.yaml"
CALENDAR_FILE = "calendar.yaml"
ASSET_FILES_DIR = Path("assets") / "files"


@dataclass(frozen=True)
class CampaignConfig:
    name: str = "Campaign"
    calendar: CalendarConfig = field(default_factory=CalendarConfig)


@dataclass(frozen=True)
class ConfigIssue:
    file: str
    message: str


def _read_yaml(path: Path) -> Any:
    yaml = YAML(typ="safe", pure=True)
    return yaml.load(StringIO(path.read_text(encoding="utf-8")))


class CampaignRepository:
    """Read access to one campaign folder."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    def _skip(self, path: Path) -> bool:
        parts = path.relative_to(self.root).parts
        return any(part.startswith(("_", ".")) for part in parts)

    def entry_files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(p for p in self.root.rglob("*.md") if p.is_file() and not self._skip(p))

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def load_documents(self) -> list[Document]:
        documents: list[Document] = []
        for path in self.entry_files():
            rel = self.relative(path)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                documents.append(
                    Document(rel, {}, [], [DocumentIssue("read-error", f"cannot read file: {exc}")])
                )
                continue
            documents.append(parse_document(text, rel))
        return documents

    def load_config(self) -> tuple[CampaignConfig, list[ConfigIssue]]:
        issues: list[ConfigIssue] = []
        name = self.root.name
        campaign_path = self.root / CAMPAIGN_FILE
        if campaign_path.is_file():
            try:
                data = _read_yaml(campaign_path) or {}
                name = str(data.get("name") or name)
            except (YAMLError, AttributeError) as exc:
                issues.append(ConfigIssue(CAMPAIGN_FILE, f"invalid campaign config: {exc}"))
        calendar = CalendarConfig()
        calendar_path = self.root / CALENDAR_FILE
        if calendar_path.is_file():
            try:
                calendar = CalendarConfig.from_dict(_read_yaml(calendar_path))
            except (YAMLError, CalendarError, KeyError, TypeError, ValueError) as exc:
                issues.append(ConfigIssue(CALENDAR_FILE, f"invalid calendar config: {exc}"))
        return CampaignConfig(name, calendar), issues

    def asset_file(self, relative_file: str) -> Path | None:
        """Resolve an asset's ``file`` value safely inside ``assets/files``."""
        base = (self.root / ASSET_FILES_DIR).resolve()
        candidate = (base / relative_file).resolve()
        if not candidate.is_relative_to(base) or not candidate.is_file():
            return None
        return candidate

    def fingerprint(self) -> tuple[tuple[str, int], ...]:
        """Changes whenever an entry or config file is added, removed, or modified."""
        files = [*self.entry_files()]
        for name in (CAMPAIGN_FILE, CALENDAR_FILE):
            candidate = self.root / name
            if candidate.is_file():
                files.append(candidate)
        return tuple((self.relative(p), p.stat().st_mtime_ns) for p in files)
