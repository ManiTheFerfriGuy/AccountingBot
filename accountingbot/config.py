"""Configuration loading for AccountingBot."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from .database import DatabaseBackupConfig
from .secrets import load_secrets

load_secrets()


@dataclass(slots=True)
class BotConfig:
    token: str
    database_path: str = "accounting.db"
    log_file: str = "accounting_bot.log"
    backup: DatabaseBackupConfig = field(default_factory=DatabaseBackupConfig)

    @classmethod
    def from_env(cls) -> "BotConfig":
        token = os.getenv("BOT_TOKEN")
        if not token:
            raise RuntimeError("BOT_TOKEN environment variable is required")
        db_path = os.getenv("DATABASE_PATH", "accounting.db")
        log_file = os.getenv("LOG_FILE", "accounting_bot.log")
        backup = DatabaseBackupConfig(
            enabled=_parse_bool(os.getenv("DB_BACKUP_ENABLED"), True),
            directory=os.getenv("DB_BACKUP_DIR", "Database_Backups"),
            compress_after_days=_parse_optional_positive_int(
                os.getenv("DB_BACKUP_COMPRESS_AFTER_DAYS"),
                default=7,
            ),
            retention_limit=_parse_optional_positive_int(
                os.getenv("DB_BACKUP_RETENTION_LIMIT"),
                default=30,
            ),
        )
        return cls(
            token=token,
            database_path=db_path,
            log_file=log_file,
            backup=backup,
        )


def load_config() -> BotConfig:
    return BotConfig.from_env()


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _parse_optional_positive_int(value: Optional[str], *, default: Optional[int]) -> Optional[int]:
    if value is None or value.strip() == "":
        return default
    try:
        parsed = int(value)
    except ValueError as exc:  # pragma: no cover - validation guard
        raise ValueError(f"Invalid integer value: {value!r}") from exc
    if parsed <= 0:
        return None
    return parsed
