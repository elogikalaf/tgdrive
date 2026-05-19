from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any

from bot.database.models import User


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_sync(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    google_refresh_token TEXT,
                    google_access_token TEXT,
                    google_folder_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    code_verifier TEXT NOT NULL DEFAULT '',
                    expires_at INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._ensure_column(conn, "oauth_states", "code_verifier", "TEXT NOT NULL DEFAULT ''")
            conn.commit()
        os.chmod(self.path, 0o600)

    async def get_user(self, telegram_id: int) -> User | None:
        return await asyncio.to_thread(self._get_user_sync, telegram_id)

    def _get_user_sync(self, telegram_id: int) -> User | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        return User(**dict(row)) if row else None

    async def upsert_user_tokens(
        self,
        telegram_id: int,
        access_token: str | None,
        refresh_token: str | None,
    ) -> None:
        await asyncio.to_thread(
            self._upsert_user_tokens_sync,
            telegram_id,
            access_token,
            refresh_token,
        )

    def _upsert_user_tokens_sync(
        self,
        telegram_id: int,
        access_token: str | None,
        refresh_token: str | None,
    ) -> None:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT google_refresh_token FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            effective_refresh_token = refresh_token
            if existing and not effective_refresh_token:
                effective_refresh_token = existing["google_refresh_token"]

            conn.execute(
                """
                INSERT INTO users (telegram_id, google_access_token, google_refresh_token)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    google_access_token = excluded.google_access_token,
                    google_refresh_token = COALESCE(excluded.google_refresh_token, users.google_refresh_token)
                """,
                (telegram_id, access_token, effective_refresh_token),
            )
            conn.commit()

    async def update_access_token(self, telegram_id: int, access_token: str | None) -> None:
        await asyncio.to_thread(self.update_access_token_sync, telegram_id, access_token)

    def update_access_token_sync(self, telegram_id: int, access_token: str | None) -> None:
        self._execute("UPDATE users SET google_access_token = ? WHERE telegram_id = ?", (access_token, telegram_id))

    async def set_folder(self, telegram_id: int, folder_id: str | None) -> None:
        await asyncio.to_thread(
            self._execute,
            """
            INSERT INTO users (telegram_id, google_folder_id)
            VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET google_folder_id = excluded.google_folder_id
            """,
            (telegram_id, folder_id),
        )

    async def disconnect(self, telegram_id: int) -> None:
        await asyncio.to_thread(
            self._execute,
            """
            UPDATE users
            SET google_refresh_token = NULL, google_access_token = NULL
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )

    async def save_oauth_state(self, state: str, telegram_id: int, code_verifier: str, expires_at: int) -> None:
        await asyncio.to_thread(
            self._execute,
            """
            INSERT OR REPLACE INTO oauth_states (state, telegram_id, code_verifier, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (state, telegram_id, code_verifier, expires_at),
        )

    async def consume_oauth_state(self, state: str, now: int) -> tuple[int, str] | None:
        return await asyncio.to_thread(self._consume_oauth_state_sync, state, now)

    def _consume_oauth_state_sync(self, state: str, now: int) -> tuple[int, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT telegram_id, code_verifier, expires_at FROM oauth_states WHERE state = ?",
                (state,),
            ).fetchone()
            conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            conn.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now,))
            conn.commit()
        if not row or int(row["expires_at"]) < now:
            return None
        return int(row["telegram_id"]), str(row["code_verifier"])

    def _execute(self, query: str, params: tuple[Any, ...]) -> None:
        with self._connect() as conn:
            conn.execute(query, params)
            conn.commit()

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
