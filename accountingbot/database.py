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
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import AsyncIterator, List, Optional, Sequence, Tuple
from zipfile import ZIP_DEFLATED, ZipFile

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DatabaseBackupConfig:
    """Configuration options for automatic database backups."""

    enabled: bool = True
    directory: str = "Database_Backups"
    compress_after_days: Optional[int] = 7
    retention_limit: Optional[int] = 30


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
    amount: int
    description: str
    created_at: datetime

    @property
    def is_payment(self) -> bool:
        return self.amount < 0


@dataclass(slots=True)
class SearchResult:
    """Represents a single person match returned by ``search_people``."""

    person: Person
    balance: int
    score: float
    matched_keywords: Tuple[str, ...]


@dataclass(slots=True)
class SearchResponse:
    """Full response returned by ``search_people`` including suggestions."""

    query: str
    matches: List[SearchResult]
    suggestions: List[str]


@dataclass(slots=True)
class PersonUsageStats:
    """Represents a person along with usage statistics."""

    person: Person
    usage_count: int
    balance: int


@dataclass(slots=True)
class DashboardTotals:
    """Aggregated totals used on the dashboard screen."""

    total_debt: int
    total_payments: int
    outstanding_balance: int


@dataclass(slots=True)
class DebtorSummary:
    """Represents a debtor entry with their outstanding balance."""

    person: Person
    balance: int


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


def _to_int(value: Optional[float | int]) -> int:
    """Normalize SQLite numeric outputs to integers."""

    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return int(round(value))


class InvalidPersonNameError(ValueError):
    """Raised when a provided person name is empty or invalid."""


class PersonAlreadyExistsError(ValueError):
    """Raised when trying to create a person with a duplicate name."""


class Database:
    """Async wrapper around SQLite for bot operations."""

    def __init__(
        self,
        db_path: str | os.PathLike[str] = "accounting.db",
        backup_config: Optional[DatabaseBackupConfig] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._lock = asyncio.Lock()
        self._backup_config = backup_config or DatabaseBackupConfig()
        self._background_tasks: set[asyncio.Task[None]] = set()

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
        self._schedule_backup()
        LOGGER.info("Added person %s with id %s", name, person_id)
        return await self.get_person(person_id)

    async def rename_person(self, person_id: int, new_name: str) -> Person:
        clean_name = new_name.strip()
        if not clean_name:
            raise InvalidPersonNameError("Name cannot be empty")
        async with self._connection() as conn:
            try:
                cursor = await asyncio.to_thread(
                    conn.execute,
                    "UPDATE people SET name = ? WHERE id = ?",
                    (clean_name, person_id),
                )
                await asyncio.to_thread(conn.commit)
            except sqlite3.IntegrityError as exc:
                raise PersonAlreadyExistsError(clean_name) from exc
        if cursor.rowcount == 0:
            raise ValueError(f"Person {person_id} does not exist")
        self._schedule_backup()
        LOGGER.info("Renamed person %s to %s", person_id, clean_name)
        person = await self.get_person(person_id)
        if not person:
            raise ValueError(f"Person {person_id} does not exist")
        return person

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
        balance_filter: Optional[Tuple[str, Optional[int]]] = None

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
                balance_filter = (">", 0)
                continue
            if normalized in {"creditors", "balance<0", "negative"}:
                balance_filter = ("<", 0)
                continue
            if normalized in {"settled", "balance=0", "zero"}:
                balance_filter = ("=", 0)
                continue
            balance_match = balance_pattern.fullmatch(normalized)
            if balance_match:
                op = balance_match.group("op")
                raw_value = float(balance_match.group("value"))
                if raw_value.is_integer():
                    balance_filter = (op, int(raw_value))
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
                params.append(operand if operand is not None else 0)
            elif operator == "<":
                sql.append("HAVING balance < ?")
                params.append(operand if operand is not None else 0)
            elif operator == ">=":
                sql.append("HAVING balance >= ?")
                params.append(operand if operand is not None else 0)
            elif operator == "<=":
                sql.append("HAVING balance <= ?")
                params.append(operand if operand is not None else 0)
            elif operator == "=":
                sql.append("HAVING ABS(balance - ?) < 1e-6")
                params.append(operand if operand is not None else 0)

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
                balance = _to_int(row["balance"] or 0)
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

    async def list_people_with_usage(self) -> List[PersonUsageStats]:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                """
                    SELECT
                        p.id,
                        p.name,
                        p.created_at,
                        COUNT(t.id) AS usage_count,
                        COALESCE(SUM(t.amount), 0) AS balance
                    FROM people p
                    LEFT JOIN transactions t ON t.person_id = p.id
                    GROUP BY p.id
                """,
            )
            rows = await asyncio.to_thread(cursor.fetchall)

        stats = [
            PersonUsageStats(
                person=Person(
                    id=row["id"],
                    name=row["name"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
                usage_count=int(row["usage_count"] or 0),
                balance=_to_int(row["balance"]),
            )
            for row in rows
        ]

        max_usage = max((entry.usage_count for entry in stats), default=0)
        if max_usage > 0:
            stats.sort(
                key=lambda entry: (
                    -entry.usage_count,
                    -abs(entry.balance),
                    entry.person.name.casefold(),
                    entry.person.id,
                )
            )
        else:
            stats.sort(
                key=lambda entry: (
                    -abs(entry.balance),
                    entry.person.name.casefold(),
                    entry.person.id,
                )
            )

        return stats

    async def add_transaction(
        self, person_id: int, amount: int, description: str = ""
    ) -> Transaction:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                "INSERT INTO transactions (person_id, amount, description) VALUES (?, ?, ?)",
                (person_id, amount, description.strip()),
            )
            await asyncio.to_thread(conn.commit)
            transaction_id = cursor.lastrowid
        self._schedule_backup()
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
            amount=_to_int(result["amount"]),
            description=result["description"],
            created_at=datetime.fromisoformat(result["created_at"]),
        )

    async def get_balance(self, person_id: int) -> int:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                "SELECT COALESCE(SUM(amount), 0) as balance FROM transactions WHERE person_id = ?",
                (person_id,),
            )
            row = await asyncio.to_thread(cursor.fetchone)
        return _to_int(row["balance"] if row and row["balance"] is not None else 0)

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
            params.append(start_date.isoformat(sep=" "))
        if end_date:
            query.append("AND created_at <= ?")
            params.append(end_date.isoformat(sep=" "))

        query.append("ORDER BY created_at DESC")
        sql = " ".join(query)

        async with self._connection() as conn:
            cursor = await asyncio.to_thread(conn.execute, sql, tuple(params))
            rows = await asyncio.to_thread(cursor.fetchall)
        return [
            Transaction(
                id=row["id"],
                person_id=row["person_id"],
                amount=_to_int(row["amount"]),
                description=row["description"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    async def get_transaction_timestamps(self, person_id: int) -> List[datetime]:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                """
                SELECT created_at
                FROM transactions
                WHERE person_id = ?
                ORDER BY created_at
                """,
                (person_id,),
            )
            rows = await asyncio.to_thread(cursor.fetchall)
        return [datetime.fromisoformat(row["created_at"]) for row in rows]

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
        self._schedule_backup()
        LOGGER.info("Set language for user %s to %s", user_id, language)

    async def wait_for_pending_tasks(self) -> None:
        """Block until all background maintenance tasks have completed."""

        while self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

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

    async def total_debt(self) -> int:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE amount > 0",
            )
            row = await asyncio.to_thread(cursor.fetchone)
        return _to_int(row[0] if row and row[0] is not None else 0)

    def _resolve_backup_directory(self) -> Path:
        directory = Path(self._backup_config.directory)
        if not directory.is_absolute():
            directory = (self.db_path.parent or Path.cwd()) / directory
        return directory

    def _schedule_backup(self) -> None:
        if not self._backup_config.enabled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            LOGGER.warning("No running event loop to schedule database backup")
            return

        task = loop.create_task(self._run_backup())
        self._background_tasks.add(task)

        def _on_done(completed: asyncio.Task[None]) -> None:
            self._background_tasks.discard(completed)
            try:
                completed.result()
            except Exception:  # pragma: no cover - safety net
                LOGGER.exception("Database backup task failed")

        task.add_done_callback(_on_done)

    async def _run_backup(self) -> None:
        try:
            await asyncio.to_thread(self._perform_backup_and_maintenance)
        except Exception:  # pragma: no cover - safety net
            LOGGER.exception("Unexpected error while performing database backup")

    def _perform_backup_and_maintenance(self) -> None:
        backup_dir = self._resolve_backup_directory()
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_path = backup_dir / f"Database_{timestamp}.db"

        try:
            self._backup_database(backup_path)
        except Exception as exc:
            LOGGER.exception("Failed to create database backup at %s", backup_path)
            raise exc

        log_path = self._format_backup_log_path(backup_dir, backup_path)
        LOGGER.info("Database backup created: %s", log_path)

        try:
            self._compress_old_backups(backup_dir)
        except Exception:
            LOGGER.exception("Failed to compress old database backups")

        try:
            self._enforce_retention_limit(backup_dir)
        except Exception:
            LOGGER.exception("Failed to enforce backup retention policy")

    def _backup_database(self, destination: Path) -> None:
        with sqlite3.connect(self.db_path) as source, sqlite3.connect(destination) as dest:
            source.backup(dest)

    def _format_backup_log_path(self, backup_dir: Path, backup_path: Path) -> str:
        try:
            return backup_path.relative_to(backup_dir.parent).as_posix()
        except ValueError:
            return backup_path.as_posix()

    def _compress_old_backups(self, backup_dir: Path) -> None:
        days = self._backup_config.compress_after_days
        if days is None or days <= 0:
            return

        cutoff = datetime.now() - timedelta(days=days)
        for db_file in sorted(backup_dir.glob("*.db")):
            try:
                mtime = datetime.fromtimestamp(db_file.stat().st_mtime)
            except OSError:
                continue
            if mtime > cutoff:
                continue

            zip_path = db_file.with_suffix(".zip")
            try:
                with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
                    archive.write(db_file, arcname=db_file.name)
                db_file.unlink()
                LOGGER.info("Compressed database backup: %s", self._format_backup_log_path(backup_dir, zip_path))
            except Exception:
                LOGGER.exception("Failed to compress backup %s", db_file)

    def _enforce_retention_limit(self, backup_dir: Path) -> None:
        limit = self._backup_config.retention_limit
        if limit is None or limit <= 0:
            return

        try:
            files = sorted(
                [
                    path
                    for path in backup_dir.iterdir()
                    if path.suffix.lower() in {".db", ".zip"}
                ],
                key=lambda item: item.stat().st_mtime,
            )
        except FileNotFoundError:
            return

        while len(files) > limit:
            oldest = files.pop(0)
            try:
                oldest.unlink()
                LOGGER.info(
                    "Deleted old database backup: %s",
                    self._format_backup_log_path(backup_dir, oldest),
                )
            except FileNotFoundError:
                continue
            except OSError:
                LOGGER.exception("Failed to delete old backup %s", oldest)

    async def total_payments(self) -> int:
        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE amount < 0",
            )
            row = await asyncio.to_thread(cursor.fetchone)
        return _to_int(row[0] if row and row[0] is not None else 0)

    async def export_transactions(
        self,
        *,
        amount_filter: Optional[str] = None,
        person_ids: Optional[Sequence[int]] = None,
    ) -> Sequence[sqlite3.Row]:
        """Return transactions ordered by date with optional filters."""

        conditions: list[str] = []
        params: list[object] = []

        if amount_filter == "debt":
            conditions.append("t.amount > 0")
        elif amount_filter == "payment":
            conditions.append("t.amount < 0")

        if person_ids:
            placeholders = ",".join("?" for _ in person_ids)
            conditions.append(f"t.person_id IN ({placeholders})")
            params.extend(person_ids)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with self._connection() as conn:
            cursor = await asyncio.to_thread(
                conn.execute,
                " ".join(
                    [
                        "SELECT",
                        "    t.id,",
                        "    t.person_id,",
                        "    p.name AS person_name,",
                        "    t.amount,",
                        "    t.description,",
                        "    t.created_at",
                        "FROM transactions t",
                        "JOIN people p ON p.id = t.person_id",
                        where_clause,
                        "ORDER BY t.created_at DESC, t.id DESC",
                    ]
                ),
                tuple(params),
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
            total_debt=_to_int(totals_row["total_debt"] if totals_row else 0),
            total_payments=_to_int(totals_row["total_payments"] if totals_row else 0),
            outstanding_balance=_to_int(totals_row["outstanding"] if totals_row else 0),
        )

        top_debtors = [
            DebtorSummary(
                person=Person(
                    id=row["id"],
                    name=row["name"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
                balance=_to_int(row["balance"]),
            )
            for row in debtor_rows
        ]

        recent_transactions = [
            RecentActivity(
                transaction=Transaction(
                    id=row["id"],
                    person_id=row["person_id"],
                    amount=_to_int(row["amount"]),
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
