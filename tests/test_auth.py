"""Accounts, password hashing, sessions, and the login slow-down."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from chronicler import auth, db
from chronicler.auth import AuthError, AuthStore
from chronicler.db import Database


@pytest.fixture
def store(tmp_path: Path) -> AuthStore:
    return AuthStore(Database(tmp_path / "_data"))


def test_migrations_run_once(tmp_path: Path) -> None:
    database = Database(tmp_path)
    assert database.version() == len(db.MIGRATIONS)
    Database(tmp_path)  # opening again is a no-op
    assert database.version() == len(db.MIGRATIONS)


def test_failed_migration_applies_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Database(tmp_path)
    monkeypatch.setattr(db, "MIGRATIONS", (*db.MIGRATIONS, "CREATE TABLE t (x); BROKEN SQL;"))
    with pytest.raises(sqlite3.Error):
        Database(tmp_path)
    database = Database.__new__(Database)
    database.path = tmp_path / db.DB_FILE
    assert database.version() == len(db.MIGRATIONS) - 1
    with database.connect() as c:
        assert c.execute("SELECT name FROM sqlite_master WHERE name = 't'").fetchone() is None


def test_accounts(store: AuthStore) -> None:
    assert not store.has_users()
    gm = store.create_user("Heather GM", "correct horse", "gm")
    store.create_user("jo", "battery staple", "player")
    assert store.has_users() and gm.gm
    assert [(u.username, u.role) for u in store.users()] == [
        ("Heather GM", "gm"),
        ("jo", "player"),
    ]
    assert store.get("JO") is not None  # names are not case-sensitive
    with store.db.connect() as c:
        stored = c.execute("SELECT password_hash FROM users WHERE username = 'jo'").fetchone()[0]
    assert stored.startswith("$argon2id$") and "battery" not in stored


@pytest.mark.parametrize(
    ("username", "password", "role", "message"),
    [
        ("", "longenough", "gm", "names are"),
        ("x" * 41, "longenough", "gm", "names are"),
        ("ok", "short", "gm", "at least 8"),
        ("ok", "longenough", "admin", "role must be"),
    ],
)
def test_account_rules(
    store: AuthStore, username: str, password: str, role: str, message: str
) -> None:
    with pytest.raises(AuthError, match=message):
        store.create_user(username, password, role)


def test_duplicate_and_removal_rules(store: AuthStore) -> None:
    gm = store.create_user("gm", "longenough", "gm")
    player = store.create_user("pc", "longenough", "player")
    with pytest.raises(AuthError, match="already has"):
        store.create_user("GM", "longenough", "player")
    with pytest.raises(AuthError, match="your own"):
        store.remove_user("gm", acting=gm)
    with pytest.raises(AuthError, match="last GM"):
        store.remove_user("gm", acting=player)
    store.remove_user("pc", acting=gm)
    with pytest.raises(AuthError, match="no account"):
        store.remove_user("pc")


def test_login_and_sessions(store: AuthStore) -> None:
    user = store.create_user("gm", "longenough", "gm")
    assert store.authenticate("gm", "wrong pass").user is None
    assert store.authenticate("nobody", "longenough").user is None
    result = store.authenticate(" gm ", "longenough")
    assert result.user == user
    token = store.start_session(user)
    assert store.session_user(token) == user
    assert store.session_user("forged") is None and store.session_user(None) is None
    with store.db.connect() as c:
        assert c.execute("SELECT token_hash FROM sessions").fetchone()[0] != token
    store.end_session(token)
    assert store.session_user(token) is None


def test_password_reset_logs_out_everywhere(store: AuthStore) -> None:
    user = store.create_user("gm", "longenough", "gm")
    token = store.start_session(user)
    store.set_password("gm", "a new password")
    assert store.session_user(token) is None
    assert store.authenticate("gm", "a new password").user == user


def _shift_session(store: AuthStore, days: float) -> None:
    with store.db.connect() as c:
        moment = dt.datetime.now(dt.UTC) + dt.timedelta(days=days)
        c.execute("UPDATE sessions SET expires_at = ?", (moment.isoformat(timespec="seconds"),))


def test_sessions_expire_and_renew(store: AuthStore) -> None:
    user = store.create_user("gm", "longenough", "gm")
    token = store.start_session(user)
    _shift_session(store, 2)  # used again: renewed to a full 30 days
    assert store.session_user(token) == user
    with store.db.connect() as c:
        expires = dt.datetime.fromisoformat(
            c.execute("SELECT expires_at FROM sessions").fetchone()[0]
        )
    assert expires - dt.datetime.now(dt.UTC) > dt.timedelta(days=29)
    _shift_session(store, -1)
    assert store.session_user(token) is None


def test_repeated_failures_slow_down(store: AuthStore, monkeypatch: pytest.MonkeyPatch) -> None:
    store.create_user("gm", "longenough", "gm")
    for _ in range(auth.FREE_ATTEMPTS - 1):
        assert store.authenticate("gm", "nope nope").retry_after == 0
    blocked = store.authenticate("gm", "nope nope")
    assert blocked.user is None and blocked.retry_after == 2
    assert store.authenticate("gm", "longenough").retry_after > 0  # even the right password
    later = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=3)
    monkeypatch.setattr(auth, "_now", lambda: later)
    assert store.authenticate("gm", "longenough").user is not None  # and the count resets
    assert store.authenticate("gm", "nope nope").retry_after == 0
