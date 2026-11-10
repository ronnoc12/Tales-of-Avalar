from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from chronicler.schema import (
    ENTRY_MODELS,
    TYPE_FOLDERS,
    Npc,
    Session,
    is_secret_entry,
    known_fields,
    to_score,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(2.5, "2.50"), ("-10", "-10.00"), (10, "10.00"), (0, "0.00"), ("3.25", "3.25"), (0.1, "0.10")],
)
def test_scores_are_exact_two_place_decimals(raw: Any, expected: str) -> None:
    assert to_score(raw) == Decimal(expected)
    assert str(to_score(raw)) == expected


@pytest.mark.parametrize("raw", [10.01, -10.5, 1.005, "abc", True, None, float("nan")])
def test_invalid_scores(raw: Any) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        to_score(raw)


def test_scores_add_exactly() -> None:
    assert to_score(0.1) + to_score(0.2) == to_score(0.3)


def _npc(**extra: Any) -> dict[str, Any]:
    return {"id": "x", "type": "npc", "name": "X", **extra}


def test_npc_accepts_full_quick_card() -> None:
    npc = Npc.model_validate(
        _npc(
            alignment="neutral good",
            party_role="ally",
            disposition=2.5,
            life_state="alive",
            **{"class": [{"name": "Rogue", "level": 7}]},
            stat_block={"ref": {"source": "Monster Manual", "name": "Spy", "page": 330}},
            factions=[{"faction": "[[USE]]", "standing": 8}],
        )
    )
    assert npc.alignment == "Neutral Good"
    assert npc.disposition == Decimal("2.50")
    assert npc.class_[0].level == 7
    assert npc.factions[0].standing == Decimal("8.00")


@pytest.mark.parametrize(
    "bad",
    [
        {"alignment": "Chaotic Stupid"},
        {"party_role": "bestie"},
        {"life_state": "sleepy"},
        {"disposition": 11},
        {"id": "Has Spaces"},
        {"status": "maybe"},
        {"class": [{"name": "Rogue", "level": 21}]},
    ],
)
def test_npc_rejects_bad_values(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Npc.model_validate(_npc(**bad))


def test_unknown_fields_are_kept_not_lost() -> None:
    npc = Npc.model_validate(_npc(favourite_food="pie"))
    assert npc.model_extra == {"favourite_food": "pie"}


def test_session_changes_allow_full_swing() -> None:
    session = Session.model_validate(
        {
            "id": "s",
            "type": "session",
            "name": "S",
            "number": 1,
            "changes": [{"who": "[[X]]", "disposition": -20}],
        }
    )
    assert session.changes[0].disposition == Decimal("-20.00")


def test_every_type_has_folder_and_model() -> None:
    assert set(ENTRY_MODELS) == set(TYPE_FOLDERS)
    assert len(set(TYPE_FOLDERS.values())) == len(TYPE_FOLDERS)


def test_known_fields_use_aliases() -> None:
    assert "class" in known_fields("npc")
    assert "class_" not in known_fields("npc")
    assert "from" not in known_fields("npc")  # nested alias is not top-level


def test_default_secret_types_and_override() -> None:
    assert is_secret_entry({"type": "pc"})
    assert not is_secret_entry({"type": "pc", "secret": False})
    assert is_secret_entry({"type": "npc", "secret": True})
    assert not is_secret_entry({"type": "npc"})
