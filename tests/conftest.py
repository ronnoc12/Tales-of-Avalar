"""Shared fixtures. ``tests/fixtures/campaign`` plants a unique ``CANARY-`` string in every
kind of secret so leak tests can prove nothing secret reaches the Player view."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from chronicler.campaign import Campaign

FIXTURE = Path(__file__).parent / "fixtures" / "campaign"
CANARY = "CANARY"


@pytest.fixture(scope="session")
def fixture_campaign() -> Campaign:
    return Campaign(FIXTURE)


MakeCampaign = Callable[[dict[str, str]], Campaign]


@pytest.fixture
def make_campaign(tmp_path: Path) -> MakeCampaign:
    """Build a throwaway campaign from {relative path: file text}."""

    def build(files: dict[str, str]) -> Campaign:
        for rel, text in files.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return Campaign(tmp_path)

    return build


def entry(entry_id: str, entry_type: str, body: str = "", **fields: str) -> str:
    """Minimal entry file text. Extra fields are raw YAML lines."""
    lines = [
        "---",
        f"id: {entry_id}",
        f"type: {entry_type}",
        f"name: {fields.pop('name', entry_id)}",
    ]
    lines += [f"{key}: {value}" for key, value in fields.items()]
    lines += ["---", body]
    return "\n".join(lines)
