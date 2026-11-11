"""Editor drafts: unsaved work, kept in ``<campaign>/_drafts/`` (git-ignored).

One draft per entry, stored as ``<entry-id>.md`` in exactly the format the entry file
would have, so it can be opened, compared, or copied over in any editor. A sidecar
``<entry-id>.json`` records which version of the entry file the draft started from (its
content fingerprint), so a save can tell when the file was changed outside the app.

When a save hits such a conflict, the draft moves to ``_drafts/conflicts/`` under a
timestamped name and the editor reloads the file from disk.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from chronicler.schema import ID_PATTERN
from chronicler.storage import DRAFTS_DIR, CampaignRepository, StorageError, atomic_write

_ID_RE = re.compile(ID_PATTERN)
CONFLICTS_DIR = "conflicts"
_STAMP = "%Y-%m-%d_%H%M%S"
_CONFLICT_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}_\d{6}(?:-\d+)?\.md$")


@dataclass(frozen=True)
class DraftInfo:
    entry_id: str
    rel_path: str  # the entry file this draft is for
    base_hash: str | None  # fingerprint of that file when editing started (None: new entry)
    saved_at: str  # ISO timestamp (local time)


@dataclass(frozen=True)
class Draft:
    info: DraftInfo
    text: str


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class DraftStore:
    def __init__(self, repository: CampaignRepository) -> None:
        self.root = repository.private_dir(DRAFTS_DIR)

    def _paths(self, entry_id: str) -> tuple[Path, Path]:
        if not _ID_RE.match(entry_id):
            raise StorageError(f"not an entry id: {entry_id!r}")
        return self.root / f"{entry_id}.md", self.root / f"{entry_id}.json"

    def get(self, entry_id: str) -> Draft | None:
        text_path, info_path = self._paths(entry_id)
        if not text_path.is_file() or not info_path.is_file():
            return None
        try:
            info = DraftInfo(**json.loads(info_path.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            return None
        return Draft(info, text_path.read_bytes().decode("utf-8"))

    def save(
        self,
        entry_id: str,
        rel_path: str,
        text: str,
        base_hash: str | None,
    ) -> DraftInfo:
        text_path, info_path = self._paths(entry_id)
        info = DraftInfo(entry_id, rel_path, base_hash, _now())
        atomic_write(text_path, text.encode("utf-8"))
        atomic_write(info_path, json.dumps(asdict(info), indent=2).encode("utf-8"))
        return info

    def discard(self, entry_id: str) -> bool:
        removed = False
        for path in self._paths(entry_id):
            if path.is_file():
                path.unlink()
                removed = True
        return removed

    def move_to_conflicts(self, entry_id: str, text: str) -> Path:
        """Keep work that could not be saved; returns where it went."""
        stamp = _dt.datetime.now().strftime(_STAMP)
        path = self.root / CONFLICTS_DIR / f"{entry_id}-{stamp}.md"
        counter = 1
        while path.exists():
            counter += 1
            path = self.root / CONFLICTS_DIR / f"{entry_id}-{stamp}-{counter}.md"
        atomic_write(path, text.encode("utf-8"))
        self.discard(entry_id)
        return path

    def files_for(self, entry_id: str) -> list[Path]:
        """Every draft file belonging to an entry, including saved conflict copies."""
        files = [p for p in self._paths(entry_id) if p.is_file()]
        conflicts = self.root / CONFLICTS_DIR
        if conflicts.is_dir():
            files += sorted(
                p
                for p in conflicts.glob(f"{entry_id}-*.md")
                if p.name.startswith(f"{entry_id}-")
                and _CONFLICT_SUFFIX_RE.fullmatch(p.name[len(entry_id) :])
            )
        return files

    def delete_all(self, entry_id: str) -> list[Path]:
        files = self.files_for(entry_id)
        for path in files:
            path.unlink(missing_ok=True)
        return files

    def list(self) -> list[DraftInfo]:
        if not self.root.is_dir():
            return []
        drafts = [self.get(p.stem) for p in sorted(self.root.glob("*.json"))]
        return [d.info for d in drafts if d is not None]
