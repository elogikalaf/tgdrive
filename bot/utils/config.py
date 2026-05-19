from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _allowed_ids() -> set[int] | None:
    raw = os.getenv("ALLOWED_TELEGRAM_IDS", "").strip()
    if not raw:
        return None
    return {int(item.strip()) for item in raw.split(",") if item.strip()}


@dataclass(frozen=True)
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_bot_token: str
    google_client_secrets_file: Path
    oauth_redirect_uri: str
    oauth_host: str
    oauth_port: int
    database_path: Path
    download_dir: Path
    token_dir: Path
    log_level: str
    allowed_telegram_ids: set[int] | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_api_id=int(_required("TELEGRAM_API_ID")),
            telegram_api_hash=_required("TELEGRAM_API_HASH"),
            telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
            google_client_secrets_file=Path(
                os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials/client_secret.json")
            ),
            oauth_redirect_uri=_required("OAUTH_REDIRECT_URI"),
            oauth_host=os.getenv("OAUTH_HOST", "127.0.0.1"),
            oauth_port=_optional_int("OAUTH_PORT", 8000),
            database_path=Path(os.getenv("DATABASE_PATH", "tokens/drivebot.sqlite3")),
            download_dir=Path(os.getenv("DOWNLOAD_DIR", "downloads")),
            token_dir=Path(os.getenv("TOKEN_DIR", "tokens")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            allowed_telegram_ids=_allowed_ids(),
        )


settings = Settings.from_env()
