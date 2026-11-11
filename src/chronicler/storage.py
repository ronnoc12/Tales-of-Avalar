"""Storage: the single place that reads and writes campaign files on disk.

Everything else works with :class:`Document` objects and file text from here, so the
storage backend could later be swapped (for example, for a database) without touching
other modules. Writes are atomic: a crash never leaves half a file behind.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import time
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
TEMPLATES_DIR = "_templates"
DRAFTS_DIR = "_drafts"
DATA_DIR = "_data"
_REPLACE_ATTEMPTS = 5


class StorageError(OSError):
    """A path outside the campaign, or a write the operating system refused."""


def atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temp file in the same folder, then swap it in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed below before the swap
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(handle.name, path)
                return
            except PermissionError:
                # Windows: a sync client or virus scanner may hold the file for a moment.
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except OSError as exc:
        Path(handle.name).unlink(missing_ok=True)
        raise StorageError(f"could not write {path.name}: {exc}") from exc


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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

    # -- entry files: read, write, delete ------------------------------------------------

    def entry_path(self, rel: str) -> Path:
        """The absolute path for an entry file, refusing anything outside the campaign."""
        candidate = (self.root / rel).resolve()
        if (
            not candidate.is_relative_to(self.root)
            or candidate.suffix != ".md"
            or candidate == self.root
            or self._skip(candidate)
        ):
            raise StorageError(f"not an entry file path: {rel}")
        return candidate

    def read_entry(self, rel: str) -> str:
        """The file's exact text (line endings kept)."""
        return self.entry_path(rel).read_bytes().decode("utf-8")

    def entry_hash(self, rel: str) -> str | None:
        """Content fingerprint used to detect edits made outside the app."""
        path = self.entry_path(rel)
        return content_hash(path.read_bytes()) if path.is_file() else None

    def write_entry(self, rel: str, text: str) -> None:
        atomic_write(self.entry_path(rel), text.encode("utf-8"))

    def delete_entry(self, rel: str) -> None:
        path = self.entry_path(rel)
        try:
            path.unlink()
        except OSError as exc:
            raise StorageError(f"could not delete {rel}: {exc}") from exc

    def uncommitted(self, rel: str) -> bool | None:
        """Does git hold changes to this file it could not restore? ``None`` if unknown."""
        path = self.entry_path(rel)
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", "--", path.name],
                cwd=path.parent,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return bool(result.stdout.strip())

    def template_text(self, entry_type: str) -> str | None:
        path = self.root / TEMPLATES_DIR / f"{entry_type}.md"
        return path.read_bytes().decode("utf-8") if path.is_file() else None

    def private_dir(self, name: str) -> Path:
        """A git-ignored working folder inside the campaign (drafts, database)."""
        if name not in (DRAFTS_DIR, DATA_DIR):
            raise StorageError(f"unknown private folder: {name}")
        return self.root / name

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
