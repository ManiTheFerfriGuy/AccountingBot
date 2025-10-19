"""Configuration loading for AccountingBot."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .secrets import load_secrets

load_secrets()


@dataclass(slots=True)
class BotConfig:
    token: str
    database_path: str = "accounting.db"
    log_file: str = "accounting_bot.log"

    @classmethod
    def from_env(cls) -> "BotConfig":
        token = os.getenv("BOT_TOKEN")
        if not token:
            raise RuntimeError("BOT_TOKEN environment variable is required")
        db_path = os.getenv("DATABASE_PATH", "accounting.db")
        log_file = os.getenv("LOG_FILE", "accounting_bot.log")
        return cls(token=token, database_path=db_path, log_file=log_file)


def load_config() -> BotConfig:
    return BotConfig.from_env()
