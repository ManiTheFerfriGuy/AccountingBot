"""Database utilities for AccountingBot."""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, List, Optional, Sequence

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Person:
    """Represents a person stored in the database."""

    id: int
    name: str
    created_at: datetime


@dataclass(slots=True)
class Transaction:
    """Represents a debt or payment transaction."""

    id: int
    person_id: int
    amount: float
    description: str
    created_at: datetime

    @property
    def is_payment(self) -> bool:
        return self.amount < 0


class Database:
    """Async wrapper around SQLite for bot operations."""

    def __init__(self, db_path: str | os.PathLike[str] = "accounting.db") -> None:
        self.db_path = Path(db_path)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize the database schema."""

        async with self._connection() as conn:
            await asyncio.to_thread(self._apply_pragmas, conn)
            await asyncio.to_thread(
                conn.executescript,
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS people (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                    amount REAL NOT NULL,
                    description TEXT DEFAULT "",
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    language TEXT NOT NULL DEFAULT 'en',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """,
            )
            await asyncio.to_thread(conn.commit)
        LOGGER.info("Database initialized at %s", self.db_path)

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[sqlite3.Connection]:
        async with self._lock:
            conn = await asyncio.to_thread(self._connect)
            try:
                yield conn
            finally:
                await asyncio.to_thread(conn.close)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    async def add_person(self, name: str) -> Person:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                "INSERT INTO people (name) VALUES (?)",
                (name.strip(),),
            )
            await asyncio.to_thread(conn.commit)
            person_id = cursor.lastrowid
        LOGGER.info("Added person %s with id %s", name, person_id)
        return await self.get_person(person_id)

    async def get_person(self, person_id: int) -> Optional[Person]:
        async with self._connection() as conn:
            row = await asyncio.to_thread(
                conn.execute,
                "SELECT id, name, created_at FROM people WHERE id = ?",
                (person_id,),
            )
            result = await asyncio.to_thread(row.fetchone)
        if not result:
            return None
        return Person(
            id=result["id"],
            name=result["name"],
            created_at=datetime.fromisoformat(result["created_at"]),
        )

    async def search_people(self, query: str, limit: int = 25) -> List[Person]:
        like = f"%{query.strip()}%"
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                """
                SELECT id, name, created_at
                FROM people
                WHERE name LIKE ? OR CAST(id AS TEXT) LIKE ?
                ORDER BY name ASC
                LIMIT ?
                """,
                (like, like, limit),
            )
            rows = await asyncio.to_thread(cursor.fetchall)
        return [
            Person(
                id=row["id"],
                name=row["name"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    async def list_people(self, limit: int = 50, offset: int = 0) -> List[Person]:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                """
                SELECT id, name, created_at
                FROM people
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            rows = await asyncio.to_thread(cursor.fetchall)
        return [
            Person(
                id=row["id"],
                name=row["name"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    async def add_transaction(
        self, person_id: int, amount: float, description: str = ""
    ) -> Transaction:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                "INSERT INTO transactions (person_id, amount, description) VALUES (?, ?, ?)",
                (person_id, amount, description.strip()),
            )
            await asyncio.to_thread(conn.commit)
            transaction_id = cursor.lastrowid
        LOGGER.info(
            "Added transaction for person_id=%s amount=%s description=%s",
            person_id,
            amount,
            description,
        )
        return await self.get_transaction(transaction_id)

    async def get_transaction(self, transaction_id: int) -> Optional[Transaction]:
        async with self._connection() as conn:
            row = await asyncio.to_thread(
                conn.execute,
                """
                SELECT id, person_id, amount, description, created_at
                FROM transactions
                WHERE id = ?
                """,
                (transaction_id,),
            )
            result = await asyncio.to_thread(row.fetchone)
        if not result:
            return None
        return Transaction(
            id=result["id"],
            person_id=result["person_id"],
            amount=result["amount"],
            description=result["description"],
            created_at=datetime.fromisoformat(result["created_at"]),
        )

    async def get_balance(self, person_id: int) -> float:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                "SELECT COALESCE(SUM(amount), 0) as balance FROM transactions WHERE person_id = ?",
                (person_id,),
            )
            row = await asyncio.to_thread(cursor.fetchone)
        return float(row["balance"] if row and row["balance"] is not None else 0.0)

    async def get_history(
        self,
        person_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Transaction]:
        query = [
            "SELECT id, person_id, amount, description, created_at",
            "FROM transactions",
            "WHERE person_id = ?",
        ]
        params: List[object] = [person_id]

        if start_date:
            query.append("AND created_at >= ?")
            params.append(start_date.isoformat())
        if end_date:
            query.append("AND created_at <= ?")
            params.append(end_date.isoformat())

        query.append("ORDER BY created_at DESC")
        sql = " ".join(query)

        async with self._connection() as conn:
            cursor = await asyncio.to_thread(conn.execute, sql, tuple(params))
            rows = await asyncio.to_thread(cursor.fetchall)
        return [
            Transaction(
                id=row["id"],
                person_id=row["person_id"],
                amount=row["amount"],
                description=row["description"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    async def set_user_language(self, user_id: int, language: str) -> None:
        async with self._connection() as conn:
            await asyncio.to_thread(
                conn.execute,
                """
                INSERT INTO user_settings (user_id, language)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET language=excluded.language, updated_at=CURRENT_TIMESTAMP
                """,
                (user_id, language),
            )
            await asyncio.to_thread(conn.commit)
        LOGGER.info("Set language for user %s to %s", user_id, language)

    async def get_user_language(self, user_id: int) -> str:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                "SELECT language FROM user_settings WHERE user_id = ?",
                (user_id,),
            )
            row = await asyncio.to_thread(cursor.fetchone)
        if row and row["language"]:
            return row["language"]
        return "en"

    async def delete_person(self, person_id: int) -> None:
        async with self._connection() as conn:
            await asyncio.to_thread(
                conn.execute,
                "DELETE FROM people WHERE id = ?",
                (person_id,),
            )
            await asyncio.to_thread(conn.commit)
        LOGGER.info("Deleted person %s", person_id)

    async def total_debt(self) -> float:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE amount > 0",
            )
            row = await asyncio.to_thread(cursor.fetchone)
        return float(row[0] if row and row[0] is not None else 0.0)

    async def total_payments(self) -> float:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE amount < 0",
            )
            row = await asyncio.to_thread(cursor.fetchone)
        return float(row[0] if row and row[0] is not None else 0.0)

    async def export_transactions(self) -> Sequence[sqlite3.Row]:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                "SELECT * FROM transactions ORDER BY created_at DESC",
            )
            rows = await asyncio.to_thread(cursor.fetchall)
        return rows
