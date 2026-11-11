"""Accounts, roles, and login sessions.

Two roles: a **GM** has full access (GM view, editing, every tool); a **Player** only ever
sees the Player view and cannot edit. Roles are enforced on the server, at every route.

* Passwords are hashed with Argon2id (``argon2-cffi``); plain passwords are never stored.
* A login session is a random token in a cookie; the database stores only its SHA-256,
  so a copied database cannot be used to log in. Sessions last 30 days and the clock
  restarts when they are used (at most one write per hour).
* Repeated wrong passwords for a name make the next attempt wait (5 free tries, then
  2, 4, 8 … seconds, up to 5 minutes).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Literal, cast

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from chronicler.db import Database

Role = Literal["gm", "player"]
ROLES: tuple[Role, ...] = ("gm", "player")
SESSION_DAYS = 30
FREE_ATTEMPTS = 5
MAX_WAIT_SECONDS = 300
MIN_PASSWORD = 8
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")
_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("timing-equalizer")


class AuthError(ValueError):
    """A request the account rules do not allow (message is safe to show)."""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: Role

    @property
    def gm(self) -> bool:
        return self.role == "gm"


@dataclass(frozen=True)
class LoginResult:
    user: User | None
    retry_after: int = 0  # seconds to wait before trying this name again


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _iso(moment: _dt.datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"passwords need at least {MIN_PASSWORD} characters")


def _user(row: object) -> User:
    data = cast(dict[str, object], row)
    return User(int(str(data["id"])), str(data["username"]), cast(Role, str(data["role"])))


class AuthStore:
    def __init__(self, database: Database) -> None:
        self.db = database

    # -- accounts -------------------------------------------------------------------------

    def has_users(self) -> bool:
        with self.db.connect() as c:
            return c.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def users(self) -> list[User]:
        with self.db.connect() as c:
            rows = c.execute("SELECT id, username, role FROM users ORDER BY username").fetchall()
        return [_user(dict(row)) for row in rows]

    def get(self, username: str) -> User | None:
        with self.db.connect() as c:
            row = c.execute(
                "SELECT id, username, role FROM users WHERE username = ?", (username.strip(),)
            ).fetchone()
        return _user(dict(row)) if row else None

    def create_user(self, username: str, password: str, role: str) -> User:
        username = username.strip()
        if not _USERNAME_RE.match(username):
            raise AuthError("names are 1-40 letters, digits, spaces, dots, dashes, or underscores")
        if role not in ROLES:
            raise AuthError("role must be gm or player")
        _check_password(password)
        if self.get(username) is not None:
            raise AuthError(f"{username!r} already has an account")
        with self.db.connect() as c:
            cursor = c.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                (username, _hasher.hash(password), role, _iso(_now())),
            )
            user_id = int(cursor.lastrowid or 0)
        return User(user_id, username, role)

    def set_password(self, username: str, password: str) -> None:
        _check_password(password)
        user = self._require(username)
        with self.db.connect() as c:
            c.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (_hasher.hash(password), user.id),
            )
            c.execute("DELETE FROM sessions WHERE user_id = ?", (user.id,))  # log out everywhere

    def remove_user(self, username: str, *, acting: User | None = None) -> None:
        user = self._require(username)
        if acting is not None and acting.id == user.id:
            raise AuthError("you cannot remove your own account")
        if user.gm and sum(u.gm for u in self.users()) == 1:
            raise AuthError("the last GM account cannot be removed")
        with self.db.connect() as c:
            c.execute("DELETE FROM users WHERE id = ?", (user.id,))

    def _require(self, username: str) -> User:
        user = self.get(username)
        if user is None:
            raise AuthError(f"no account named {username!r}")
        return user

    # -- logging in -----------------------------------------------------------------------

    def _wait_for(self, username: str) -> int:
        with self.db.connect() as c:
            row = c.execute(
                "SELECT count, last_at FROM login_failures WHERE username = ?", (username,)
            ).fetchone()
        if row is None or row["count"] < FREE_ATTEMPTS:
            return 0
        wait = min(2 ** (row["count"] - FREE_ATTEMPTS + 1), MAX_WAIT_SECONDS)
        elapsed = (_now() - _dt.datetime.fromisoformat(row["last_at"])).total_seconds()
        return max(0, int(wait - elapsed + 0.999))

    def authenticate(self, username: str, password: str) -> LoginResult:
        username = username.strip()
        wait = self._wait_for(username)
        if wait:
            return LoginResult(None, wait)
        with self.db.connect() as c:
            row = c.execute(
                "SELECT id, username, role, password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        stored = row["password_hash"] if row else _DUMMY_HASH  # same work either way
        try:
            _hasher.verify(stored, password)
            ok = row is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False
        if not ok:
            with self.db.connect() as c:
                c.execute(
                    "INSERT INTO login_failures (username, count, last_at) VALUES (?, 1, ?) "
                    "ON CONFLICT(username) DO UPDATE SET count = count + 1, last_at = ?",
                    (username, _iso(_now()), _iso(_now())),
                )
            return LoginResult(None, self._wait_for(username))  # read after the commit
        with self.db.connect() as c:
            c.execute("DELETE FROM login_failures WHERE username = ?", (username,))
            if _hasher.check_needs_rehash(stored):
                c.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (_hasher.hash(password), row["id"]),
                )
        return LoginResult(_user(dict(row)))

    # -- sessions -------------------------------------------------------------------------

    def start_session(self, user: User) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        with self.db.connect() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(now),))
            c.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
                "VALUES (?,?,?,?)",
                (
                    _token_hash(token),
                    user.id,
                    _iso(now),
                    _iso(now + _dt.timedelta(days=SESSION_DAYS)),
                ),
            )
        return token

    def session_user(self, token: str | None) -> User | None:
        if not token:
            return None
        now = _now()
        with self.db.connect() as c:
            row = c.execute(
                "SELECT u.id, u.username, u.role, s.expires_at FROM sessions s "
                "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (_token_hash(token),),
            ).fetchone()
            if row is None:
                return None
            expires = _dt.datetime.fromisoformat(row["expires_at"])
            if expires <= now:
                c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
                return None
            renewed = now + _dt.timedelta(days=SESSION_DAYS)
            if renewed - expires > _dt.timedelta(hours=1):
                c.execute(
                    "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                    (_iso(renewed), _token_hash(token)),
                )
        return _user(dict(row))

    def end_session(self, token: str | None) -> None:
        if token:
            with self.db.connect() as c:
                c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
