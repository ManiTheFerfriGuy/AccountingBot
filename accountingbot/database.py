"""Database utilities for AccountingBot."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import AsyncIterator, List, Optional, Sequence, Tuple

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


@dataclass(slots=True)
class SearchResult:
    """Represents a single person match returned by ``search_people``."""

    person: Person
    balance: float
    score: float
    matched_keywords: Tuple[str, ...]


@dataclass(slots=True)
class SearchResponse:
    """Full response returned by ``search_people`` including suggestions."""

    query: str
    matches: List[SearchResult]
    suggestions: List[str]


@dataclass(slots=True)
class DashboardTotals:
    """Aggregated totals used on the dashboard screen."""

    total_debt: float
    total_payments: float
    outstanding_balance: float


@dataclass(slots=True)
class DebtorSummary:
    """Represents a debtor entry with their outstanding balance."""

    person: Person
    balance: float


@dataclass(slots=True)
class RecentActivity:
    """Represents a recent transaction alongside the person's name."""

    transaction: Transaction
    person_name: str


@dataclass(slots=True)
class DashboardSummary:
    """Container for dashboard information."""

    totals: DashboardTotals
    top_debtors: List[DebtorSummary]
    recent_transactions: List[RecentActivity]


class InvalidPersonNameError(ValueError):
    """Raised when a provided person name is empty or invalid."""


class PersonAlreadyExistsError(ValueError):
    """Raised when trying to create a person with a duplicate name."""


class Database:
    """Async wrapper around SQLite for bot operations."""

    def __init__(self, db_path: str | os.PathLike[str] = "accounting.db") -> None:
        self.db_path = Path(db_path)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize the database schema."""

        async with self._connection() as conn:
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

                CREATE INDEX IF NOT EXISTS idx_people_name ON people(name COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_transactions_person_created_at
                    ON transactions(person_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_transactions_created_at
                    ON transactions(created_at DESC);
                """,
            )
            await asyncio.to_thread(conn.commit)
        LOGGER.info("Database initialized at %s", self.db_path)

    def _configure_connection(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA temp_store = MEMORY;")

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
        self._configure_connection(conn)
        return conn

    async def add_person(self, name: str) -> Person:
        clean_name = name.strip()
        if not clean_name:
            raise InvalidPersonNameError("Name cannot be empty")
        async with self._connection() as conn:
            try:
                cursor = await asyncio.to_thread(
                    conn.execute,
                    "INSERT INTO people (name) VALUES (?)",
                    (clean_name,),
                )
                await asyncio.to_thread(conn.commit)
            except sqlite3.IntegrityError as exc:
                raise PersonAlreadyExistsError(clean_name) from exc
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

    async def search_people(self, query: str, limit: int = 25) -> SearchResponse:
        """Search for people using fuzzy matching and optional filters.

        The query accepts special tokens:

        - ``balance>NUMBER`` / ``balance<NUMBER`` / ``balance=NUMBER``
        - ``debtors`` (alias for ``balance>0``)
        - ``creditors`` (alias for ``balance<0``)

        Returns a :class:`SearchResponse` containing scored matches and
        optional suggestions.
        """

        query = query.strip()
        tokens = [token for token in re.split(r"\s+", query) if token]
        ids: List[int] = []
        keywords: List[str] = []
        balance_filter: Optional[Tuple[str, Optional[float]]] = None

        balance_pattern = re.compile(r"balance(?P<op>[<>]=?|=)(?P<value>-?\d+(?:\.\d+)?)")

        for token in tokens:
            normalized = token.strip().lower()
            if not normalized:
                continue
            if normalized.startswith("#"):
                normalized = normalized[1:]
            if normalized.isdigit():
                ids.append(int(normalized))
                continue
            if normalized in {"debtors", "balance>0", "positive"}:
                balance_filter = (">", 0.0)
                continue
            if normalized in {"creditors", "balance<0", "negative"}:
                balance_filter = ("<", 0.0)
                continue
            if normalized in {"settled", "balance=0", "zero"}:
                balance_filter = ("=", 0.0)
                continue
            balance_match = balance_pattern.fullmatch(normalized)
            if balance_match:
                op = balance_match.group("op")
                value = float(balance_match.group("value"))
                balance_filter = (op, value)
                continue
            keywords.append(normalized)

        fetch_limit = max(limit * 4, 50)

        sql = [
            "SELECT p.id, p.name, p.created_at, COALESCE(SUM(t.amount), 0) AS balance",
            "FROM people p",
            "LEFT JOIN transactions t ON t.person_id = p.id",
        ]
        params: List[object] = []
        conditions: List[str] = []

        if ids:
            placeholders = ",".join("?" for _ in ids)
            conditions.append(f"p.id IN ({placeholders})")
            params.extend(ids)

        for keyword in keywords:
            conditions.append("LOWER(p.name) LIKE ?")
            params.append(f"%{keyword}%")

        if conditions:
            sql.append("WHERE " + " AND ".join(conditions))

        sql.append("GROUP BY p.id")

        if balance_filter:
            operator, operand = balance_filter
            if operator == ">":
                sql.append("HAVING balance > ?")
                params.append(operand if operand is not None else 0.0)
            elif operator == "<":
                sql.append("HAVING balance < ?")
                params.append(operand if operand is not None else 0.0)
            elif operator == ">=":
                sql.append("HAVING balance >= ?")
                params.append(operand if operand is not None else 0.0)
            elif operator == "<=":
                sql.append("HAVING balance <= ?")
                params.append(operand if operand is not None else 0.0)
            elif operator == "=":
                sql.append("HAVING ABS(balance - ?) < 1e-6")
                params.append(operand if operand is not None else 0.0)

        sql.append("ORDER BY ABS(balance) DESC, p.name ASC")
        sql.append("LIMIT ?")
        params.append(fetch_limit)

        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute, " ".join(sql), tuple(params)
            )
            rows = await asyncio.to_thread(cursor.fetchall)

            matches: List[SearchResult] = []
            suggestions: List[str] = []

            norm_query = _normalize_text(" ".join(keywords) if keywords else query)
            for row in rows:
                person = Person(
                    id=row["id"],
                    name=row["name"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                balance = float(row["balance"] or 0.0)
                norm_name = _normalize_text(person.name)
                matched_keywords = tuple(
                    keyword for keyword in keywords if keyword in norm_name
                )
                base_score = (
                    SequenceMatcher(None, norm_name, norm_query).ratio()
                    if norm_query
                    else 1.0
                )
                if ids and person.id in ids:
                    base_score += 0.6
                prefix_bonus = sum(0.15 for keyword in matched_keywords if norm_name.startswith(keyword))
                containment_bonus = sum(0.05 for keyword in matched_keywords)
                score = base_score + prefix_bonus + containment_bonus
                matches.append(
                    SearchResult(
                        person=person,
                        balance=balance,
                        score=score,
                        matched_keywords=matched_keywords,
                    )
                )

            matches.sort(
                key=lambda item: (
                    -item.score,
                    -abs(item.balance),
                    item.person.name.casefold(),
                )
            )
            matches = matches[:limit]

            if not matches and keywords:
                suggest_cursor = await asyncio.to_thread(
                    conn.execute,
                    "SELECT name FROM people ORDER BY created_at DESC LIMIT ?",
                    (200,),
                )
                suggest_rows = await asyncio.to_thread(suggest_cursor.fetchall)
                scored_suggestions: List[Tuple[float, str]] = []
                for suggestion_row in suggest_rows:
                    suggestion_name = suggestion_row["name"]
                    ratio = SequenceMatcher(
                        None, _normalize_text(suggestion_name), norm_query
                    ).ratio()
                    if ratio >= 0.45:
                        scored_suggestions.append((ratio, suggestion_name))
                scored_suggestions.sort(key=lambda item: item[0], reverse=True)
                suggestions = [name for _, name in scored_suggestions[:5]]

        return SearchResponse(query=query, matches=matches, suggestions=suggestions)

    async def list_people(
        self, limit: Optional[int] = None, offset: int = 0
    ) -> List[Person]:
        async with self._connection() as conn:
            query = """
                SELECT id, name, created_at
                FROM people
                ORDER BY created_at DESC
            """
            params: Tuple[object, ...] = ()
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params = (limit, offset)
            cursor = await asyncio.to_thread(
                conn.execute,
                query,
                params,
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

    async def get_dashboard_summary(
        self, top: int = 3, recent: int = 5
    ) -> DashboardSummary:
        """Return aggregated information used for the dashboard view."""

        async with self._connection() as conn:
            totals_cursor = await asyncio.to_thread(
                conn.execute,
                """
                SELECT
                    COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) AS total_debt,
                    COALESCE(SUM(CASE WHEN amount < 0 THEN amount END), 0) AS total_payments,
                    COALESCE(SUM(amount), 0) AS outstanding
                FROM transactions
                """,
            )
            totals_row = await asyncio.to_thread(totals_cursor.fetchone)

            debtors_cursor = await asyncio.to_thread(
                conn.execute,
                """
                SELECT
                    p.id,
                    p.name,
                    p.created_at,
                    COALESCE(SUM(t.amount), 0) AS balance
                FROM people p
                LEFT JOIN transactions t ON t.person_id = p.id
                GROUP BY p.id
                HAVING balance > 0
                ORDER BY balance DESC, p.name ASC
                LIMIT ?
                """,
                (top,),
            )
            debtor_rows = await asyncio.to_thread(debtors_cursor.fetchall)

            recent_cursor = await asyncio.to_thread(
                conn.execute,
                """
                SELECT
                    t.id,
                    t.person_id,
                    p.name AS person_name,
                    t.amount,
                    t.description,
                    t.created_at
                FROM transactions t
                JOIN people p ON p.id = t.person_id
                ORDER BY t.created_at DESC
                LIMIT ?
                """,
                (recent,),
            )
            recent_rows = await asyncio.to_thread(recent_cursor.fetchall)

        totals = DashboardTotals(
            total_debt=float(totals_row["total_debt"]) if totals_row else 0.0,
            total_payments=float(totals_row["total_payments"]) if totals_row else 0.0,
            outstanding_balance=float(totals_row["outstanding"]) if totals_row else 0.0,
        )

        top_debtors = [
            DebtorSummary(
                person=Person(
                    id=row["id"],
                    name=row["name"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
                balance=float(row["balance"] or 0.0),
            )
            for row in debtor_rows
        ]

        recent_transactions = [
            RecentActivity(
                transaction=Transaction(
                    id=row["id"],
                    person_id=row["person_id"],
                    amount=row["amount"],
                    description=row["description"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
                person_name=row["person_name"],
            )
            for row in recent_rows
        ]

        return DashboardSummary(
            totals=totals,
            top_debtors=top_debtors,
            recent_transactions=recent_transactions,
        )


def _normalize_text(value: str) -> str:
    """Normalize a string for case-insensitive fuzzy comparisons."""

    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return without_marks.casefold()
