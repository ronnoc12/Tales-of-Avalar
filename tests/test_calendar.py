from __future__ import annotations

import pytest

from chronicler.calendar import Calendar, CalendarConfig, CalendarError, Era, WorldDate


@pytest.fixture
def cal() -> Calendar:
    return Calendar(
        CalendarConfig.from_dict(
            {
                "months": [f"M{i}" for i in range(1, 13)],
                "weekdays": [
                    "Moonday",
                    "Tideday",
                    "Windday",
                    "Thunderday",
                    "Fireday",
                    "Starday",
                    "Sunday",
                ],
                "eras": [
                    {"name": "Darkness", "start": 0, "end": 600},
                    {"name": "Modern", "start": 600},
                ],
            }
        )
    )


def test_defaults_are_12_by_30_with_7_day_week() -> None:
    config = CalendarConfig()
    assert len(config.months) == 12
    assert config.days_per_month == 30
    assert config.days_per_year == 360
    assert len(config.weekdays) == 7
    assert [s.months for s in config.seasons] == [(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]


def test_parse_and_format_round_trip_includes_weekday(cal: Calendar) -> None:
    date = cal.parse("14 M3 1487 AS")
    assert date == WorldDate(1487, 3, 14)
    text = cal.format(date)
    assert text.endswith("14 M3 1487 AS")
    assert text.split(",")[0] in cal.config.weekdays
    assert cal.parse(text) == date


def test_weekday_advances_one_per_day_and_wraps(cal: Calendar) -> None:
    start = WorldDate(1500, 1, 1)
    names = [cal.weekday(cal.add_days(start, n)) for n in range(8)]
    assert names[0] == names[7]
    assert len(set(names[:7])) == 7


def test_epoch_weekday_shifts_names() -> None:
    a = Calendar(CalendarConfig(epoch_weekday=0))
    b = Calendar(CalendarConfig(epoch_weekday=2))
    day = WorldDate(0, 1, 1)
    assert a.weekday_index(day) == 0
    assert b.weekday_index(day) == 2


def test_wrong_weekday_is_rejected(cal: Calendar) -> None:
    date = WorldDate(1500, 2, 10)
    actual = cal.weekday(date)
    wrong = next(d for d in cal.config.weekdays if d != actual)
    assert cal.parse(f"{actual}, 10 M2 1500 AS") == date
    with pytest.raises(CalendarError, match=f"is a {actual}"):
        cal.parse(f"{wrong}, 10 M2 1500 AS")


def test_month_rollover_and_day_math(cal: Calendar) -> None:
    assert cal.add_days(WorldDate(1500, 1, 30), 1) == WorldDate(1500, 2, 1)
    assert cal.add_days(WorldDate(1500, 12, 30), 1) == WorldDate(1501, 1, 1)
    assert cal.days_between(WorldDate(1500, 1, 1), WorldDate(1501, 1, 1)) == 360


def test_before_epoch_years_are_negative(cal: Calendar) -> None:
    date = cal.parse("1 M1 300 BS")
    assert date.year == -300
    assert cal.format(date, weekday=False) == "1 M1 300 BS"
    assert cal.add_days(cal.parse("30 M12 1 BS"), 1) == WorldDate(0, 1, 1)


@pytest.mark.parametrize(
    "text",
    [
        "31 M1 1500",
        "0 M1 1500",
        "1 Nomonth 1500",
        "yesterday",
        "1 M1 1500 XX",
        "Blursday, 1 M1 1500",
    ],
)
def test_invalid_dates(cal: Calendar, text: str) -> None:
    with pytest.raises(CalendarError):
        cal.parse(text)


def test_fuzzy_dates(cal: Calendar) -> None:
    circa = cal.parse_fuzzy("c. 300 AS")
    assert (circa.year, circa.approximate, circa.era) == (300, True, Era("Darkness", 0, 600))
    assert cal.format_fuzzy(circa) == "c. 300 AS"
    year = cal.parse_fuzzy("700")
    assert year.year == 700 and not year.approximate and year.era and year.era.name == "Modern"
    era = cal.parse_fuzzy("darkness")
    assert era.year is None and era.sort_year == 0 and cal.format_fuzzy(era) == "Darkness"
    exact = cal.parse_fuzzy("2 M2 10 AS")
    assert exact.exact == WorldDate(10, 2, 2)
    with pytest.raises(CalendarError):
        cal.parse_fuzzy("the before times")


def test_season_and_era_lookup(cal: Calendar) -> None:
    assert cal.season(WorldDate(1, 4, 1)) == "Summer"
    assert cal.era_of(599) == Era("Darkness", 0, 600)
    assert cal.era_of(-5) is None


@pytest.mark.parametrize(
    "data",
    [
        {"epoch_weekday": 9},
        {"seasons": [{"name": "Odd", "months": [13]}]},
        {"days_per_month": 0},
    ],
)
def test_invalid_config(data: dict[str, object]) -> None:
    with pytest.raises(CalendarError):
        CalendarConfig.from_dict(data)


def test_empty_config_uses_defaults() -> None:
    assert CalendarConfig.from_dict(None) == CalendarConfig()
