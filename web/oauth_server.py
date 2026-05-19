from __future__ import annotations

import logging

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse

from bot.services.notifier import TelegramNotifier
from bot.services.oauth_service import OAuthService


logger = logging.getLogger(__name__)


def create_oauth_app(oauth_service: OAuthService, notifier: TelegramNotifier, redirect_uri: str) -> FastAPI:
    app = FastAPI(title="Drivebot OAuth Callback")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/google/callback", response_class=HTMLResponse)
    async def oauth_callback(
        request: Request,
        state: str = Query(...),
        error: str | None = Query(None),
    ) -> str:
        if error:
            logger.warning("Google OAuth returned an error for state=%s", state)
            return _html("Google Drive connection failed", "You can close this page and try /connect again.")

        authorization_response = f"{redirect_uri}?{request.url.query}"
        telegram_id = await oauth_service.finish_callback(state, authorization_response)
        if telegram_id is None:
            logger.warning("Rejected OAuth callback with invalid or expired state")
            return _html("Invalid or expired login link", "Go back to Telegram and run /connect again.")

        await notifier.send_message(telegram_id, "Google Drive connected successfully.")
        logger.info("Google Drive connected for telegram_id=%s", telegram_id)
        return _html("Google Drive connected", "You can close this page and return to Telegram.")

    return app


def _html(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:42rem;margin:4rem auto;padding:0 1rem;line-height:1.5}</style>"
        "</head><body>"
        f"<h1>{title}</h1><p>{body}</p>"
        "</body></html>"
    )
