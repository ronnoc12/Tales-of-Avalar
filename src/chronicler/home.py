"""Home page data: last session, coming up, hanging threads, prep, and spotlight."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any

from chronicler.calendar import Calendar, CalendarError, WorldDate
from chronicler.campaign import View
from chronicler.document import Document
from chronicler.markup import Link, iter_spans, parse_line, parse_text, plain_text

UPCOMING_DAYS = 7
_TASK_RE = re.compile(r"^\s*[-*+]\s+\[( |x|X)\]\s+")


@dataclass(frozen=True)
class Upcoming:
    date: WorldDate
    date_text: str
    kind: str  # "action" or "deadline"
    title: str
    entry_id: str
    entry_name: str
    days_away: int


@dataclass(frozen=True)
class Thread:
    entry_id: str
    name: str
    kind: str
    detail: str


@dataclass(frozen=True)
class PrepNote:
    entry_id: str
    entry_name: str
    anchor: str
    text: str


@dataclass(frozen=True)
class PrepTask:
    entry_id: str
    name: str
    done: int
    total: int
    state: str


@dataclass(frozen=True)
class SpotlightRow:
    entry_id: str
    name: str
    player: str | None
    sessions_since: int | None  # None = never spotlighted
    total_sessions: int


def _parse(calendar: Calendar, value: Any) -> WorldDate | None:
    if not isinstance(value, str):
        return None
    try:
        return calendar.parse(value)
    except CalendarError:
        return None


def sessions_in_order(view: View) -> list[Document]:
    def number(document: Document) -> int:
        value = document.meta.get("number")
        return value if isinstance(value, int) else -1

    return sorted(view.index.of_type("session"), key=number)


def last_session(view: View) -> Document | None:
    sessions = sessions_in_order(view)
    return sessions[-1] if sessions else None


def current_date(view: View, calendar: Calendar) -> WorldDate | None:
    """Config override if set, otherwise the latest session's in-world end date."""
    if calendar.config.current_date:
        override = _parse(calendar, calendar.config.current_date)
        if override:
            return override
    for session in reversed(sessions_in_order(view)):
        span = session.meta.get("in_world")
        if isinstance(span, dict):
            end = _parse(calendar, span.get("end")) or _parse(calendar, span.get("start"))
            if end:
                return end
    return None


def upcoming(
    view: View, calendar: Calendar, today: WorldDate, days: int = UPCOMING_DAYS
) -> tuple[list[Upcoming], list[Upcoming]]:
    """(coming up in the next ``days`` days, overdue and still pending)."""
    soon: list[Upcoming] = []
    overdue: list[Upcoming] = []

    def add(date: WorldDate, kind: str, title: str, document: Document) -> None:
        away = calendar.days_between(today, date)
        item = Upcoming(date, calendar.format(date), kind, title, document.id, document.name, away)
        if away < 0:
            overdue.append(item)
        elif away < days:
            soon.append(item)

    for plot in view.index.of_type("plotline"):
        actions = plot.meta.get("actions")
        for action in actions if isinstance(actions, list) else []:
            if not isinstance(action, dict) or action.get("result", "pending") != "pending":
                continue
            date = _parse(calendar, action.get("date"))
            if date:
                add(date, "action", str(action.get("name", "Action")), plot)
    for decision in view.index.of_type("decision"):
        if decision.meta.get("state", "pending") != "pending":
            continue
        date = _parse(calendar, decision.meta.get("deadline"))
        if date:
            add(date, "deadline", decision.name, decision)
    soon.sort(key=lambda u: (u.date, u.title))
    overdue.sort(key=lambda u: (u.date, u.title))
    return soon, overdue


def hanging_threads(view: View) -> list[Thread]:
    threads: list[Thread] = []
    for plot in view.index.of_type("plotline"):
        if plot.meta.get("state", "active") != "active":
            continue
        actions = plot.meta.get("actions")
        pending = [
            a
            for a in (actions if isinstance(actions, list) else [])
            if isinstance(a, dict) and a.get("result", "pending") == "pending"
        ]
        detail = f"{len(pending)} pending action(s)" if pending else "active, no pending actions"
        threads.append(Thread(plot.id, plot.name, "plotline", detail))
    for decision in view.index.of_type("decision"):
        if decision.meta.get("state", "pending") == "pending":
            threads.append(Thread(decision.id, decision.name, "decision", "awaiting the party"))
    for document in view.index.docs.values():
        tags = document.meta.get("tags")
        if isinstance(tags, list) and "unresolved" in [str(t) for t in tags]:
            threads.append(Thread(document.id, document.name, document.type, "marked unresolved"))
    return threads


def prep_notes(view: View) -> list[PrepNote]:
    """Every inline ``{prep}`` note, numbered in the same order the renderer anchors them."""
    notes: list[PrepNote] = []
    for document in sorted(view.index.docs.values(), key=lambda d: d.name.casefold()):
        counter = 0
        for section in document.sections:
            lines = ([section.title] if section.level else []) + [
                line for i, line in enumerate(section.lines) if not section.code[i]
            ]
            for line in lines:
                nodes, _ = parse_line(line)
                for span in iter_spans(nodes):  # same order the renderer numbers them
                    if span.kind != "prep":
                        continue
                    text = plain_text(span.children).strip()
                    if span.continued and notes and notes[-1].entry_id == document.id:
                        previous = notes[-1]
                        notes[-1] = replace(previous, text=f"{previous.text} {text}".strip())
                        continue
                    counter += 1
                    notes.append(PrepNote(document.id, document.name, f"prep-{counter}", text))
    return notes


def prep_tasks(view: View) -> list[PrepTask]:
    tasks: list[PrepTask] = []
    for document in view.index.of_type("prep"):
        state = str(document.meta.get("state", "open"))
        if state == "done":
            continue
        done = total = 0
        for section in document.sections:
            for line in section.lines:
                match = _TASK_RE.match(line)
                if match:
                    total += 1
                    done += match.group(1) != " "
        tasks.append(PrepTask(document.id, document.name, done, total, state))
    return tasks


def _links_to(view: View, text_values: list[Any], target_id: str) -> bool:
    for value in text_values:
        if not isinstance(value, str):
            continue
        for nodes, _issues in parse_text(value):
            for node in nodes:
                if isinstance(node, Link):
                    resolution = view.index.resolve_link(node.target)
                    if resolution.entry_id == target_id:
                        return True
    return False


@dataclass(frozen=True)
class Trend:
    total: Decimal
    sessions: int

    @property
    def label(self) -> str:
        direction = "warming" if self.total > 0 else ("cooling" if self.total < 0 else "steady")
        sign = "+" if self.total > 0 else ""
        return f"{direction} {sign}{self.total} over the last {self.sessions} session(s)"


def score_trend(
    view: View, entry_id: str, key: str = "disposition", window: int = 4
) -> Trend | None:
    """Sum of a score's changes recorded in the last ``window`` sessions."""
    sessions = sessions_in_order(view)[-window:]
    total = Decimal("0.00")
    found = False
    for session in sessions:
        changes = session.meta.get("changes")
        for change in changes if isinstance(changes, list) else []:
            if not isinstance(change, dict) or key not in change:
                continue
            if view.index.link_id(change.get("who")) != entry_id:
                continue
            try:
                total += Decimal(str(change[key]))
            except InvalidOperation:
                continue
            found = True
    return Trend(total.quantize(Decimal("0.01")), len(sessions)) if found else None


def spotlight(view: View) -> list[SpotlightRow]:
    """Sessions since each living PC's last spotlight moment, most-neglected first."""
    sessions = sessions_in_order(view)
    rows: list[SpotlightRow] = []
    for pc in view.index.of_type("pc"):
        if (
            pc.meta.get("life_state", "alive") != "alive"
            or pc.meta.get("status", "canon") != "canon"
        ):
            continue
        last: int | None = None
        for position, session in enumerate(sessions):
            listed = session.meta.get("spotlight")
            if isinstance(listed, list):
                hit = _links_to(view, listed, pc.id)
            else:
                hit = any(
                    ref.source_id == session.id for ref in view.index.backlinks.get(pc.id, [])
                )
            if hit:
                last = position
        since = None if last is None else len(sessions) - 1 - last
        player = pc.meta.get("player")
        rows.append(
            SpotlightRow(pc.id, pc.name, str(player) if player else None, since, len(sessions))
        )
    rows.sort(
        key=lambda r: (-(r.sessions_since if r.sessions_since is not None else 10**6), r.name)
    )
    return rows
