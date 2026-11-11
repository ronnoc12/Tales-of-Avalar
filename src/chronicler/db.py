"""The local SQLite database for data that does not live in Markdown (one owner per fact).

Today: user accounts and login sessions. Later: the reveal log, transcripts, and other
app-owned data. The file lives in the campaign's git-ignored ``_data/`` folder.

Schema changes are a numbered list of SQL scripts (:data:`MIGRATIONS`); each runs once,
in order, and the database records how far it has got. Never edit a released script:
add a new one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DB_FILE = "chronicler.db"

MIGRATIONS: tuple[str, ...] = (
    # 1: accounts and login sessions
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('gm', 'player')),
        created_at TEXT NOT NULL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE INDEX sessions_by_user ON sessions(user_id);
    CREATE TABLE login_failures (
        username TEXT PRIMARY KEY COLLATE NOCASE,
        count INTEGER NOT NULL,
        last_at TEXT NOT NULL
    );
    """,
)


class Database:
    def __init__(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / DB_FILE
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """A short-lived connection; commits on success, rolls back on error."""
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def version(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> None:
        current = self.version()
        for number, script in enumerate(MIGRATIONS, start=1):
            if number <= current:
                continue
            connection = sqlite3.connect(self.path, timeout=10)
            try:
                # executescript commits first; wrap the script so it applies all or nothing.
                connection.executescript(
                    f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;"
                )
            except sqlite3.Error:
                connection.rollback()
                raise
            finally:
                connection.close()
