import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from accountingbot.database import Database


class HistoryDateRangeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = Path(self._tmpdir.name) / "test.db"
        self.db = Database(self.db_path)
        await self.db.initialize()
        self.person = await self.db.add_person("Alice")

    async def _set_transaction_timestamp(self, transaction_id: int, timestamp: datetime) -> None:
        async with self.db._connection() as conn:  # pylint: disable=protected-access
            await asyncio.to_thread(
                conn.execute,
                "UPDATE transactions SET created_at = ? WHERE id = ?",
                (timestamp.isoformat(sep=" "), transaction_id),
            )
            await asyncio.to_thread(conn.commit)

    async def test_history_with_today_start_includes_later_records(self):
        yesterday = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        today_start = yesterday + timedelta(days=1)
        today_later = today_start + timedelta(hours=15)

        old_txn = await self.db.add_transaction(self.person.id, 10, "old")
        recent_txn = await self.db.add_transaction(self.person.id, 20, "recent")

        await self._set_transaction_timestamp(old_txn.id, yesterday + timedelta(hours=23))
        await self._set_transaction_timestamp(recent_txn.id, today_later)

        history = await self.db.get_history(self.person.id, start_date=today_start)

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].id, recent_txn.id)
        self.assertEqual(history[0].created_at, today_later)


if __name__ == "__main__":
    unittest.main()
