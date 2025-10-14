"""cPanel integration helpers."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CPanelConfig:
    host: str
    username: str
    token: str
    verify_ssl: bool = True

    @classmethod
    def from_env(cls) -> Optional["CPanelConfig"]:
        host = os.getenv("CPANEL_HOST")
        username = os.getenv("CPANEL_USERNAME")
        token = os.getenv("CPANEL_API_TOKEN")
        if not all([host, username, token]):
            return None
        verify = os.getenv("CPANEL_VERIFY_SSL", "true").lower() != "false"
        return cls(host=host, username=username, token=token, verify_ssl=verify)


class CPanelClient:
    """Simple wrapper for cPanel UAPI requests."""

    def __init__(self, config: CPanelConfig) -> None:
        self.config = config

    def request(self, module: str, function: str, **params: Any) -> Dict[str, Any]:
        url = f"https://{self.config.host}:2083/execute/{module}/{function}"
        headers = {
            "Authorization": f"cpanel {self.config.username}:{self.config.token}",
        }
        LOGGER.debug("Sending request to cPanel %s", url)
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30,
            verify=self.config.verify_ssl,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("status", 0):
            LOGGER.error("cPanel error: %s", data)
            raise RuntimeError(f"cPanel request failed: {data}")
        return data

    def push_backup(self, archive_url: str) -> Dict[str, Any]:
        """Example helper to trigger a remote backup import inside cPanel."""

        LOGGER.info("Triggering remote backup import: %s", archive_url)
        return self.request(
            "Backup",
            "fullbackup_to_remote",
            url=archive_url,
        )


def get_client() -> Optional[CPanelClient]:
    config = CPanelConfig.from_env()
    if not config:
        LOGGER.warning("cPanel configuration missing; integration disabled.")
        return None
    return CPanelClient(config)
