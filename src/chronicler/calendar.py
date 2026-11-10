"""In-world calendar: exact dates, weekdays, seasons, and fuzzy historical dates.

A year has ``len(months) * days_per_month`` days. Years are counted from an epoch event
(for Avalar: the Sundering, year 0). Years after it use ``era_suffix`` ("AS"); years before
it use ``before_suffix`` ("BS") and are stored as negative numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class CalendarError(ValueError):
    """Raised when a date string cannot be parsed or is invalid."""


@dataclass(frozen=True)
class Season:
    name: str
    months: tuple[int, ...]


@dataclass(frozen=True)
class Era:
    name: str
    start: int | None = None
    end: int | None = None


def _default_months() -> tuple[str, ...]:
    return tuple(f"Month-{i}" for i in range(1, 13))


def _default_weekdays() -> tuple[str, ...]:
    return tuple(f"Day-{i}" for i in range(1, 8))


def _default_seasons() -> tuple[Season, ...]:
    return (
        Season("Spring", (1, 2, 3)),
        Season("Summer", (4, 5, 6)),
        Season("Autumn", (7, 8, 9)),
        Season("Winter", (10, 11, 12)),
    )


@dataclass(frozen=True)
class CalendarConfig:
    months: tuple[str, ...] = field(default_factory=_default_months)
    weekdays: tuple[str, ...] = field(default_factory=_default_weekdays)
    days_per_month: int = 30
    seasons: tuple[Season, ...] = field(default_factory=_default_seasons)
    era_suffix: str = "AS"
    before_suffix: str = "BS"
    epoch_weekday: int = 0
    eras: tuple[Era, ...] = ()
    current_date: str | None = None

    @property
    def days_per_year(self) -> int:
        return len(self.months) * self.days_per_month

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CalendarConfig:
        """Build a config from parsed YAML, falling back to defaults for missing keys."""
        if not data:
            return cls()
        kwargs: dict[str, Any] = {}
        if "months" in data:
            kwargs["months"] = tuple(str(m) for m in data["months"])
        if "weekdays" in data:
            kwargs["weekdays"] = tuple(str(d) for d in data["weekdays"])
        for key in ("days_per_month", "epoch_weekday"):
            if key in data:
                kwargs[key] = int(data[key])
        for key in ("era_suffix", "before_suffix"):
            if key in data:
                kwargs[key] = str(data[key])
        if "seasons" in data:
            kwargs["seasons"] = tuple(
                Season(str(s["name"]), tuple(int(m) for m in s["months"])) for s in data["seasons"]
            )
        if "eras" in data:
            kwargs["eras"] = tuple(
                Era(
                    str(e["name"]),
                    None if e.get("start") is None else int(e["start"]),
                    None if e.get("end") is None else int(e["end"]),
                )
                for e in data["eras"]
            )
        if data.get("current_date"):
            kwargs["current_date"] = str(data["current_date"])
        config = cls(**kwargs)
        config.check()
        return config

    def check(self) -> None:
        if not self.months or not self.weekdays or self.days_per_month < 1:
            raise CalendarError("calendar needs months, weekdays, and days_per_month >= 1")
        if not 0 <= self.epoch_weekday < len(self.weekdays):
            raise CalendarError("epoch_weekday must index into weekdays")
        for season in self.seasons:
            for month in season.months:
                if not 1 <= month <= len(self.months):
                    raise CalendarError(f"season {season.name!r} has invalid month {month}")


@dataclass(frozen=True, order=True)
class WorldDate:
    """An exact in-world date. Ordering follows the calendar."""

    year: int
    month: int
    day: int


@dataclass(frozen=True)
class FuzzyDate:
    """A historical date that may be approximate ("c. 300 AS") or only an era name."""

    text: str
    year: int | None
    era: Era | None
    exact: WorldDate | None = None
    approximate: bool = False

    @property
    def sort_year(self) -> int | None:
        if self.year is not None:
            return self.year
        if self.era is not None:
            return self.era.start
        return None


_DATE_RE = re.compile(
    r"^\s*(?:(?P<weekday>[^\W\d][\w'-]*)\s*,\s*)?"
    r"(?P<day>\d{1,3})\s+(?P<month>[^\W\d][\w' -]*?)\s+(?P<year>\d+)"
    r"(?:\s+(?P<suffix>[A-Za-z]+))?\s*$"
)
_YEAR_RE = re.compile(
    r"^\s*(?P<circa>c\.|ca\.|circa|~)?\s*(?P<year>\d+)(?:\s+(?P<suffix>[A-Za-z]+))?\s*$",
    re.IGNORECASE,
)


class Calendar:
    """Date arithmetic and formatting for one campaign calendar."""

    def __init__(self, config: CalendarConfig | None = None) -> None:
        self.config = config or CalendarConfig()
        self.config.check()
        self._month_lookup = {m.casefold(): i + 1 for i, m in enumerate(self.config.months)}
        self._weekday_lookup = {d.casefold(): i for i, d in enumerate(self.config.weekdays)}

    # -- arithmetic -------------------------------------------------------------------

    def ordinal(self, date: WorldDate) -> int:
        c = self.config
        return date.year * c.days_per_year + (date.month - 1) * c.days_per_month + (date.day - 1)

    def from_ordinal(self, ordinal: int) -> WorldDate:
        c = self.config
        year, rest = divmod(ordinal, c.days_per_year)
        month_index, day_index = divmod(rest, c.days_per_month)
        return WorldDate(year, month_index + 1, day_index + 1)

    def add_days(self, date: WorldDate, days: int) -> WorldDate:
        return self.from_ordinal(self.ordinal(date) + days)

    def days_between(self, start: WorldDate, end: WorldDate) -> int:
        return self.ordinal(end) - self.ordinal(start)

    def weekday_index(self, date: WorldDate) -> int:
        return (self.ordinal(date) + self.config.epoch_weekday) % len(self.config.weekdays)

    def weekday(self, date: WorldDate) -> str:
        return self.config.weekdays[self.weekday_index(date)]

    def season(self, date: WorldDate) -> str | None:
        for season in self.config.seasons:
            if date.month in season.months:
                return season.name
        return None

    def era_of(self, year: int) -> Era | None:
        for era in self.config.eras:
            if (era.start is None or year >= era.start) and (era.end is None or year < era.end):
                return era
        return None

    # -- parsing ----------------------------------------------------------------------

    def validate(self, date: WorldDate) -> None:
        if not 1 <= date.month <= len(self.config.months):
            raise CalendarError(f"month {date.month} out of range")
        if not 1 <= date.day <= self.config.days_per_month:
            raise CalendarError(f"day {date.day} out of range (1-{self.config.days_per_month})")

    def _year_from(self, year_text: str, suffix: str | None) -> int:
        year = int(year_text)
        if suffix is None or suffix.casefold() == self.config.era_suffix.casefold():
            return year
        if suffix.casefold() == self.config.before_suffix.casefold():
            return -year
        raise CalendarError(f"unknown year suffix {suffix!r}")

    def parse(self, text: str) -> WorldDate:
        """Parse an exact date such as ``"14 Month-3 1487 AS"`` (weekday prefix optional).

        If a weekday is given it must match the computed weekday.
        """
        match = _DATE_RE.match(text)
        if not match:
            raise CalendarError(f"not a date: {text!r} (expected e.g. '14 Month-3 1487 AS')")
        month_name = match["month"].strip().casefold()
        if month_name not in self._month_lookup:
            raise CalendarError(f"unknown month {match['month'].strip()!r} in {text!r}")
        date = WorldDate(
            self._year_from(match["year"], match["suffix"]),
            self._month_lookup[month_name],
            int(match["day"]),
        )
        self.validate(date)
        if match["weekday"]:
            given = match["weekday"].casefold()
            if given not in self._weekday_lookup:
                raise CalendarError(f"unknown weekday {match['weekday']!r} in {text!r}")
            actual = self.weekday(date)
            if self._weekday_lookup[given] != self.weekday_index(date):
                raise CalendarError(f"{text!r}: that date is a {actual}, not {match['weekday']}")
        return date

    def parse_fuzzy(self, text: str) -> FuzzyDate:
        """Parse an exact date, a (circa) year, or an era name."""
        cleaned = text.strip()
        try:
            exact = self.parse(cleaned)
        except CalendarError:
            pass
        else:
            return FuzzyDate(cleaned, exact.year, self.era_of(exact.year), exact=exact)
        year_match = _YEAR_RE.match(cleaned)
        if year_match:
            year = self._year_from(year_match["year"], year_match["suffix"])
            return FuzzyDate(
                cleaned, year, self.era_of(year), approximate=bool(year_match["circa"])
            )
        for era in self.config.eras:
            if era.name.casefold() == cleaned.casefold():
                return FuzzyDate(cleaned, None, era)
        raise CalendarError(f"not a date, year, or era: {text!r}")

    # -- formatting -------------------------------------------------------------------

    def format_year(self, year: int) -> str:
        if year < 0:
            return f"{-year} {self.config.before_suffix}"
        return f"{year} {self.config.era_suffix}"

    def format(self, date: WorldDate, *, weekday: bool = True) -> str:
        """Format a date. The weekday is always included unless explicitly disabled."""
        body = f"{date.day} {self.config.months[date.month - 1]} {self.format_year(date.year)}"
        return f"{self.weekday(date)}, {body}" if weekday else body

    def format_fuzzy(self, value: FuzzyDate) -> str:
        if value.exact is not None:
            return self.format(value.exact)
        if value.year is not None:
            prefix = "c. " if value.approximate else ""
            return prefix + self.format_year(value.year)
        return value.era.name if value.era else value.text
