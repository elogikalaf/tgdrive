from __future__ import annotations

import asyncio
import logging

import uvicorn
from pyrogram import Client, idle

from bot.database.sqlite import Database
from bot.handlers.commands import register_command_handlers
from bot.handlers.media import register_media_handlers
from bot.services.google_drive import GoogleDriveService
from bot.services.notifier import TelegramNotifier
from bot.services.oauth_service import OAuthService
from bot.utils.config import settings
from bot.utils.logging import configure_logging
from web.oauth_server import create_oauth_app


logger = logging.getLogger(__name__)


async def main() -> None:
    configure_logging(settings.log_level)
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    settings.token_dir.mkdir(parents=True, exist_ok=True)
    settings.google_client_secrets_file.parent.mkdir(parents=True, exist_ok=True)
    settings.token_dir.chmod(0o700)

    database = Database(settings.database_path)
    await database.initialize()

    notifier = TelegramNotifier()
    oauth_service = OAuthService(
        database=database,
        client_secrets_file=settings.google_client_secrets_file,
        redirect_uri=settings.oauth_redirect_uri,
    )
    drive_service = GoogleDriveService(
        database=database,
        client_secrets_file=settings.google_client_secrets_file,
    )

    app = Client(
        name="drivebot",
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        bot_token=settings.telegram_bot_token,
        workdir=str(settings.token_dir),
    )
    notifier.set_client(app)

    register_command_handlers(
        app=app,
        database=database,
        oauth_service=oauth_service,
        drive_service=drive_service,
        allowed_telegram_ids=settings.allowed_telegram_ids,
    )
    register_media_handlers(
        app=app,
        drive_service=drive_service,
        download_dir=settings.download_dir,
        allowed_telegram_ids=settings.allowed_telegram_ids,
    )

    oauth_app = create_oauth_app(oauth_service, notifier, settings.oauth_redirect_uri)
    uvicorn_config = uvicorn.Config(
        oauth_app,
        host=settings.oauth_host,
        port=settings.oauth_port,
        log_level=settings.log_level.lower(),
    )
    oauth_server = uvicorn.Server(uvicorn_config)
    oauth_task = asyncio.create_task(oauth_server.serve(), name="oauth-server")

    logger.info("Starting Telegram polling and OAuth callback server")
    await app.start()
    try:
        await idle()
    finally:
        logger.info("Shutting down")
        await app.stop()
        oauth_server.should_exit = True
        await oauth_task


if __name__ == "__main__":
    asyncio.run(main())
