"""A loaded campaign with its two views (GM and Player)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from chronicler.calendar import Calendar
from chronicler.document import Document
from chronicler.index import CampaignIndex
from chronicler.redact import HiddenNameLeak, player_documents
from chronicler.render import Renderer
from chronicler.search import SearchIndex
from chronicler.storage import CampaignConfig, CampaignRepository, ConfigIssue


@dataclass
class View:
    gm: bool
    documents: list[Document]
    index: CampaignIndex
    renderer: Renderer
    _search: SearchIndex | None = field(default=None, repr=False)

    @property
    def search(self) -> SearchIndex:
        if self._search is None:
            self._search = SearchIndex(self.index)
        return self._search


class Campaign:
    def __init__(self, root: Path | str) -> None:
        self.repository = CampaignRepository(root)
        self.config: CampaignConfig
        self.config_issues: list[ConfigIssue]
        self.calendar: Calendar
        self.documents: list[Document]
        self.gm: View
        self.player: View
        self.hidden_name_leaks: list[HiddenNameLeak] = []
        self._fingerprint: tuple[tuple[str, int], ...] = ()
        self.reload()

    @property
    def root(self) -> Path:
        return self.repository.root

    @property
    def fingerprint(self) -> tuple[tuple[str, int], ...]:
        """Changes whenever the files this campaign was loaded from change."""
        return self._fingerprint

    def reload(self) -> None:
        self._fingerprint = self.repository.fingerprint()
        self.config, self.config_issues = self.repository.load_config()
        self.calendar = Calendar(self.config.calendar)
        self._build(self.repository.load_documents())

    def derive(self, documents: list[Document]) -> Campaign:
        """The same campaign with a different set of documents, built in memory only.

        Used to validate and preview proposed edits before anything is written.
        """
        clone = object.__new__(Campaign)
        clone.repository = self.repository
        clone.config = self.config
        clone.config_issues = self.config_issues
        clone.calendar = self.calendar
        clone._fingerprint = self._fingerprint
        clone._build(documents)
        return clone

    def _build(self, documents: list[Document]) -> None:
        self.documents = documents
        gm_index = CampaignIndex(self.documents)
        self.gm = View(True, self.documents, gm_index, Renderer(gm_index, self.calendar, gm=True))
        redacted = player_documents(self.documents, gm_index)
        self.hidden_name_leaks = redacted.leaks
        player_index = CampaignIndex(redacted.documents)
        self.player = View(
            False,
            redacted.documents,
            player_index,
            Renderer(player_index, self.calendar, gm=False),
        )

    def refresh_if_changed(self) -> bool:
        if self.repository.fingerprint() != self._fingerprint:
            self.reload()
            return True
        return False

    def view(self, *, gm: bool) -> View:
        return self.gm if gm else self.player
