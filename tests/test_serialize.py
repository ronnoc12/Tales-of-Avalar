"""Writing entries back to text: exact round-trips and minimal diffs."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from chronicler.document import parse_document
from chronicler.serialize import (
    EntryText,
    SerializeError,
    new_entry_text,
    normalize_entry_text,
    update_entry_text,
)
from chronicler.storage import CampaignRepository
from tests.conftest import FIXTURE

CAMPAIGN = Path(__file__).parent.parent / "campaign"

SAMPLE = (
    "---\n"
    "id: cas\n"
    "type: npc\n"
    "name: Cas  # the leader\n"
    'location: "[[Midcross]]"\n'
    "disposition: 3.00\n"
    "relationships:\n"
    '  - {to: "[[Cassie]]", type: twin}\n'
    "---\n"
    "## Appearance\n"
    "Red hair.\n"
    "\n"
    "---\n"
    "A horizontal rule in the body.\n"
)


def meta_of(text: str) -> dict[str, object]:
    return parse_document(text, "npcs/x.md").meta


def every_entry_file() -> list[Path]:
    files = CampaignRepository(FIXTURE).entry_files()
    if CAMPAIGN.is_dir():
        files += CampaignRepository(CAMPAIGN).entry_files()
    return files


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_compose_rebuilds_the_exact_text(newline: str) -> None:
    text = SAMPLE.replace("\n", newline)
    assert EntryText.parse(text).compose() == text


def test_no_op_update_is_byte_identical_for_every_entry() -> None:
    for path in every_entry_file():
        raw = path.read_bytes().decode("utf-8")
        assert update_entry_text(raw, meta=meta_of(raw)) == raw, path


def test_every_entry_is_already_tidy() -> None:
    for path in every_entry_file():
        raw = path.read_bytes().decode("utf-8")
        assert normalize_entry_text(raw) == raw, path


def test_changing_one_value_leaves_the_rest_untouched() -> None:
    meta = meta_of(SAMPLE)
    meta["disposition"] = Decimal("2.50")
    out = update_entry_text(SAMPLE, meta=meta)
    changed = [(a, b) for a, b in zip(SAMPLE.split("\n"), out.split("\n"), strict=True) if a != b]
    assert changed == [("disposition: 3.00", "disposition: 2.50")]


def test_changed_string_keeps_its_quote_style() -> None:
    meta = meta_of(SAMPLE)
    meta["location"] = "[[Valoria]]"
    assert 'location: "[[Valoria]]"' in update_entry_text(SAMPLE, meta=meta)


def test_text_with_commas_and_links_round_trips() -> None:
    meta = meta_of(SAMPLE)
    meta["relationships"] = [
        {"to": "[[Cassie]]", "type": "twin", "note": "Smooth, discreet, and neutral"},
        {"to": "[[Redaelia]]", "type": "mentor of"},
    ]
    meta["role"] = "[[USE]] leader: sharp, kind"
    out = update_entry_text(SAMPLE, meta=meta)
    assert meta_of(out) == meta
    assert "  - {to:" in out.split("relationships:")[1]  # new item copies the flow style


def test_dates_and_removed_keys() -> None:
    text = "---\nid: s\ntype: session\nname: S\nreal_date: 2026-11-09\nnumber: 3\n---\n"
    meta = meta_of(text)
    meta["real_date"] = "2026-11-10"  # the editor sends text back
    del meta["number"]
    out = update_entry_text(text, meta=meta)
    assert "real_date: 2026-11-10\n" in out and "number" not in out
    assert meta_of(out)["real_date"] == dt.date(2026, 11, 10)


def test_lists_shrink_and_comments_survive() -> None:
    meta = meta_of(SAMPLE)
    meta["relationships"] = []
    out = update_entry_text(SAMPLE, meta=meta)
    assert "# the leader" in out and meta_of(out)["relationships"] == []


def test_body_replacement_keeps_frontmatter_and_line_endings() -> None:
    text = SAMPLE.replace("\n", "\r\n")
    out = update_entry_text(text, body="## New\nBody.\n")
    assert out.endswith("---\r\n## New\r\nBody.\r\n") and out.startswith("---\r\nid: cas\r\n")


def test_new_entry_text_parses_back() -> None:
    meta = {"id": "x", "type": "npc", "name": "X", "goals": [{"text": "a, b"}]}
    text = new_entry_text(meta, "## Notes\n")
    assert meta_of(text) == meta and text.endswith("---\n## Notes\n")


@pytest.mark.parametrize(
    "text",
    ["no frontmatter", "---\nid: x\n", "---\nid: [bad\n---\n", "---\n- a list\n---\n"],
)
def test_unsafe_files_are_refused(text: str) -> None:
    with pytest.raises(SerializeError):
        update_entry_text(text, meta={"id": "x"})


def test_empty_frontmatter_loads_as_empty_mapping() -> None:
    assert dict(EntryText.parse("---\n---\nbody").load()) == {}
