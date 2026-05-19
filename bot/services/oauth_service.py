from __future__ import annotations

import asyncio
import base64
import hashlib
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
        code_verifier = secrets.token_urlsafe(64)
        expires_at = int(time.time()) + STATE_TTL_SECONDS
        await self.database.save_oauth_state(state, telegram_id, code_verifier, expires_at)

        return await asyncio.to_thread(self._build_authorization_url, state, code_verifier)

    def _build_authorization_url(self, state: str, code_verifier: str) -> str:
        flow = self._new_flow(code_verifier)
        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            code_challenge=_code_challenge(code_verifier),
            code_challenge_method="S256",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        return authorization_url

    async def finish_callback(self, state: str, authorization_response: str) -> int | None:
        oauth_state = await self.database.consume_oauth_state(state, int(time.time()))
        if oauth_state is None:
            return None
        telegram_id, code_verifier = oauth_state

        access_token, refresh_token = await asyncio.to_thread(
            self._fetch_tokens,
            authorization_response,
            code_verifier,
        )
        await self.database.upsert_user_tokens(telegram_id, access_token, refresh_token)
        return telegram_id

    def _fetch_tokens(self, authorization_response: str, code_verifier: str) -> tuple[str | None, str | None]:
        flow = self._new_flow(code_verifier)
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        return credentials.token, credentials.refresh_token

    def _new_flow(self, code_verifier: str) -> Flow:
        flow = Flow.from_client_secrets_file(
            str(self.client_secrets_file),
            scopes=SCOPES,
            redirect_uri=self.redirect_uri,
            code_verifier=code_verifier,
        )
        return flow


def _code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
