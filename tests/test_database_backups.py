import asyncio
import os
from datetime import datetime, timedelta

from accountingbot.database import Database, DatabaseBackupConfig


def test_creates_backup_after_insert(tmp_path):
    async def runner() -> None:
        db_path = tmp_path / "test.db"
        backup_dir = tmp_path / "Database_Backups"
        config = DatabaseBackupConfig(directory=str(backup_dir))

        db = Database(db_path, backup_config=config)
        await db.initialize()

        await db.add_person("Alice")
        await db.wait_for_pending_tasks()

        backups = list(backup_dir.glob("*.db"))
        assert len(backups) == 1

    asyncio.run(runner())


def test_compresses_and_prunes_old_backups(tmp_path):
    async def runner() -> None:
        db_path = tmp_path / "test.db"
        backup_dir = tmp_path / "Database_Backups"
        config = DatabaseBackupConfig(
            directory=str(backup_dir),
            compress_after_days=1,
            retention_limit=2,
        )

        db = Database(db_path, backup_config=config)
        await db.initialize()

        person = await db.add_person("Alice")
        await db.wait_for_pending_tasks()

        first_backup = next(backup_dir.glob("*.db"))
        stale_time = datetime.now() - timedelta(days=2)
        os.utime(first_backup, (stale_time.timestamp(), stale_time.timestamp()))

        await db.add_transaction(person.id, 10.0, "Initial debt")
        await db.wait_for_pending_tasks()

        zipped_files = list(backup_dir.glob("*.zip"))
        assert len(zipped_files) == 1

        await db.add_transaction(person.id, -5.0, "Partial payment")
        await db.wait_for_pending_tasks()

        relevant_files = [
            path for path in backup_dir.iterdir() if path.suffix.lower() in {".db", ".zip"}
        ]
        assert len(relevant_files) <= 2

    asyncio.run(runner())
