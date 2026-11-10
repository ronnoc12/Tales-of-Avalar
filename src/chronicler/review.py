"""``chronicler review``: after a status change, list everything that may need updating."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from chronicler.campaign import Campaign
from chronicler.document import parse_document
from chronicler.index import Mention, Reference

NON_CANON = ("retired", "idea")


class ReviewError(RuntimeError):
    """Raised when git information is unavailable."""


@dataclass(frozen=True)
class StatusChange:
    entry_id: str
    name: str
    rel_path: str
    old: str | None  # None = new entry
    new: str


@dataclass
class ReviewReport:
    entry_id: str
    name: str
    status: str
    backlinks: list[Reference] = field(default_factory=list)
    mentions: list[Mention] = field(default_factory=list)
    alternates: list[str] = field(default_factory=list)
    canon_linkers: list[Reference] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.backlinks or self.mentions or self.alternates or self.canon_linkers)


def _git(cwd: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
        )
    except FileNotFoundError as exc:
        raise ReviewError("git is not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise ReviewError(exc.stderr.strip() or f"git {' '.join(args)} failed") from exc
    return completed.stdout


def status_changes(campaign: Campaign) -> list[StatusChange]:
    """Entries whose ``status`` differs from the last commit (new entries included)."""
    root = campaign.root
    top = Path(_git(root, "rev-parse", "--show-toplevel").strip())
    try:
        _git(root, "rev-parse", "--verify", "HEAD")
        has_head = True
    except ReviewError:
        has_head = False
    porcelain = _git(root, "status", "--porcelain", "--untracked-files=all", "--", ".")
    changed: set[str] = set()
    for line in porcelain.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.add(path)
    results: list[StatusChange] = []
    for document in campaign.documents:
        absolute = (campaign.root / document.rel_path).resolve()
        repo_path = absolute.relative_to(top.resolve()).as_posix()
        if repo_path not in changed:
            continue
        new = str(document.meta.get("status", "canon"))
        old: str | None = None
        if has_head:
            try:
                previous = parse_document(_git(top, "show", f"HEAD:{repo_path}"), repo_path)
                old = str(previous.meta.get("status", "canon"))
            except ReviewError:
                old = None
        if old != new:
            results.append(StatusChange(document.id, document.name, document.rel_path, old, new))
    return results


def review_entry(campaign: Campaign, entry_id: str) -> ReviewReport:
    index = campaign.gm.index
    document = index.docs[entry_id]
    status = str(document.meta.get("status", "canon"))
    report = ReviewReport(entry_id, document.name, status)
    report.backlinks = list(index.backlinks.get(entry_id, []))
    report.mentions = [m for m in index.mentions() if m.target_id == entry_id]
    base = index.link_id(document.meta.get("alternate_of")) or entry_id
    group = {base} | {
        other_id
        for other_id, other in index.docs.items()
        if index.link_id(other.meta.get("alternate_of")) == base
    }
    report.alternates = sorted(group - {entry_id})
    if status in NON_CANON:
        report.canon_linkers = [
            ref
            for ref in report.backlinks
            if index.docs[ref.source_id].meta.get("status", "canon") == "canon"
        ]
    return report
