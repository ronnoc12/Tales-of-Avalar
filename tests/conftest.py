"""Shared fixtures. ``tests/fixtures/campaign`` plants a unique ``CANARY-`` string in every
kind of secret so leak tests can prove nothing secret reaches the Player view."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


WRITE = {"X-Chronicler": "1"}  # the header every write needs (with a JSON body)
PASSWORD = "correct horse battery"


def logged_in(app: FastAPI, role: str = "gm", username: str | None = None) -> TestClient:
    """A test client logged in as a GM or a Player (the account is created if needed)."""
    name = username or f"test-{role}"
    store = app.state.auth
    if store.get(name) is None:
        store.create_user(name, PASSWORD, role)
    client = TestClient(app)
    response = client.post("/login", json={"username": name, "password": PASSWORD}, headers=WRITE)
    assert response.status_code == 204 and response.headers["HX-Redirect"] == "/", response.text
    return client


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
