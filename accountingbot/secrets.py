"""Helpers for loading secrets into environment variables."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_SECRETS_FILE = Path(__file__).resolve().parent.parent / "secrets.json"


def load_secrets(file_path: str | os.PathLike[str] | None = None) -> None:
    """Load secrets from ``file_path`` into ``os.environ``.

    Values that already exist in ``os.environ`` are left untouched so that
    cPanel environment variables (or any other runtime configuration) take
    precedence. The secrets file must contain a single JSON object.
    """

    path = Path(file_path) if file_path is not None else DEFAULT_SECRETS_FILE
    if not path.exists():
        return

    try:
        data: Any = json.loads(path.read_text())
    except json.JSONDecodeError as exc:  # pragma: no cover - configuration guard
        raise RuntimeError(f"Could not parse secrets file: {path}") from exc

    if not isinstance(data, dict):  # pragma: no cover - configuration guard
        raise RuntimeError("Secrets file must contain a JSON object at the top level")

    for key, value in data.items():
        if value is None:
            continue
        os.environ.setdefault(str(key), str(value))
