from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path

from google_auth_oauthlib.flow import Flow

from bot.database.sqlite import Database


DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SCOPES = [DRIVE_FILE_SCOPE]
STATE_TTL_SECONDS = 15 * 60


class OAuthService:
    def __init__(
        self,
        database: Database,
        client_secrets_file: Path,
        redirect_uri: str,
    ) -> None:
        self.database = database
        self.client_secrets_file = client_secrets_file
        self.redirect_uri = redirect_uri

    async def create_authorization_url(self, telegram_id: int) -> str:
        state = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + STATE_TTL_SECONDS
        await self.database.save_oauth_state(state, telegram_id, expires_at)

        return await asyncio.to_thread(self._build_authorization_url, state)

    def _build_authorization_url(self, state: str) -> str:
        flow = self._new_flow()
        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes=True,
            prompt="consent",
            state=state,
        )
        return authorization_url

    async def finish_callback(self, state: str, authorization_response: str) -> int | None:
        telegram_id = await self.database.consume_oauth_state(state, int(time.time()))
        if telegram_id is None:
            return None

        access_token, refresh_token = await asyncio.to_thread(
            self._fetch_tokens,
            authorization_response,
        )
        await self.database.upsert_user_tokens(telegram_id, access_token, refresh_token)
        return telegram_id

    def _fetch_tokens(self, authorization_response: str) -> tuple[str | None, str | None]:
        flow = self._new_flow()
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        return credentials.token, credentials.refresh_token

    def _new_flow(self) -> Flow:
        flow = Flow.from_client_secrets_file(
            str(self.client_secrets_file),
            scopes=SCOPES,
            redirect_uri=self.redirect_uri,
        )
        return flow
