from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    telegram_id: int
    google_refresh_token: str | None
    google_access_token: str | None
    google_folder_id: str | None
    created_at: str

    @property
    def is_connected(self) -> bool:
        return bool(self.google_refresh_token)
