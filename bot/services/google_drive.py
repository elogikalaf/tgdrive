from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from bot.database.models import User
from bot.database.sqlite import Database
from bot.services.oauth_service import SCOPES


logger = logging.getLogger(__name__)


def direct_download_url(file_id: str) -> str:
    return f"https://drive.google.com/uc?id={file_id}&export=download"


class DriveNotConnectedError(RuntimeError):
    pass


class DriveAuthError(RuntimeError):
    pass


class DriveFolderError(RuntimeError):
    pass


class GoogleDriveService:
    def __init__(self, database: Database, client_secrets_file: Path) -> None:
        self.database = database
        self.client_secrets_file = client_secrets_file

    async def upload_file(self, telegram_id: int, file_path: Path, drive_name: str) -> dict[str, Any]:
        user = await self._connected_user(telegram_id)
        return await asyncio.to_thread(self._upload_file_sync, user, file_path, drive_name)

    async def list_files(self, telegram_id: int, limit: int = 10) -> list[dict[str, Any]]:
        user = await self._connected_user(telegram_id)
        return await asyncio.to_thread(self._list_files_sync, user, limit)

    async def delete_file(self, telegram_id: int, file_id: str) -> None:
        user = await self._connected_user(telegram_id)
        await asyncio.to_thread(self._delete_file_sync, user, file_id)

    async def set_upload_folder(self, telegram_id: int, folder_value: str | None) -> dict[str, str | bool | None]:
        if not folder_value or folder_value.lower() == "root":
            await self.database.set_folder(telegram_id, None)
            return {"id": None, "name": "Drive root", "created": False}

        user = await self._connected_user(telegram_id)
        folder = await asyncio.to_thread(self._resolve_or_create_folder_sync, user, folder_value)
        await self.database.set_folder(telegram_id, str(folder["id"]))
        return folder

    async def _connected_user(self, telegram_id: int) -> User:
        user = await self.database.get_user(telegram_id)
        if not user or not user.google_refresh_token:
            raise DriveNotConnectedError("Google Drive is not connected")
        return user

    def _service_for_user(self, user: User):
        credentials = self._credentials_for_user(user)
        if not credentials.valid:
            if credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                except RefreshError as exc:
                    raise DriveAuthError("Google authorization expired. Run /connect again.") from exc
                self.database.update_access_token_sync(user.telegram_id, credentials.token)
            else:
                raise DriveAuthError("Google authorization expired. Run /connect again.")

        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    def _credentials_for_user(self, user: User) -> Credentials:
        client_info = self._load_client_info()
        return Credentials(
            token=user.google_access_token,
            refresh_token=user.google_refresh_token,
            token_uri=client_info["token_uri"],
            client_id=client_info["client_id"],
            client_secret=client_info["client_secret"],
            scopes=SCOPES,
        )

    def _load_client_info(self) -> dict[str, str]:
        with self.client_secrets_file.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        info = payload.get("web") or payload.get("installed")
        if not info:
            raise RuntimeError("Google client secrets file must contain a web or installed client")
        return {
            "token_uri": info["token_uri"],
            "client_id": info["client_id"],
            "client_secret": info["client_secret"],
        }

    def _upload_file_sync(self, user: User, file_path: Path, drive_name: str) -> dict[str, Any]:
        service = self._service_for_user(user)
        metadata: dict[str, Any] = {"name": drive_name}
        if user.google_folder_id:
            metadata["parents"] = [user.google_folder_id]

        mime_type = mimetypes.guess_type(drive_name)[0] or "application/octet-stream"
        media = MediaFileUpload(
            str(file_path),
            mimetype=mime_type,
            chunksize=8 * 1024 * 1024,
            resumable=True,
        )
        request = service.files().create(
            body=metadata,
            media_body=media,
            fields="id,name,size,webViewLink,createdTime",
        )

        logger.info("Starting Drive upload for telegram_id=%s file=%s", user.telegram_id, drive_name)
        response = None
        last_logged = -1
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                if progress >= last_logged + 10 or progress == 100:
                    logger.info(
                        "Drive upload progress telegram_id=%s file=%s progress=%s%%",
                        user.telegram_id,
                        drive_name,
                        progress,
                    )
                    last_logged = progress

        logger.info("Drive upload complete telegram_id=%s file=%s", user.telegram_id, drive_name)
        file_id = response["id"]
        self._make_file_public(service, file_id, user.telegram_id)
        response["downloadLink"] = direct_download_url(file_id)
        return response

    def _list_files_sync(self, user: User, limit: int) -> list[dict[str, Any]]:
        service = self._service_for_user(user)
        folder_id = user.google_folder_id or "root"
        folder_path = self._folder_path(service, user.google_folder_id)
        query = f"trashed = false and '{folder_id}' in parents"
        result = (
            service.files()
            .list(
                q=query,
                pageSize=limit,
                orderBy="createdTime desc",
                fields="files(id,name,size,createdTime,webViewLink)",
            )
            .execute()
        )
        files = result.get("files", [])
        for item in files:
            if item.get("id"):
                item["downloadLink"] = direct_download_url(item["id"])
            item["folderPath"] = folder_path
        return files

    def _delete_file_sync(self, user: User, file_id: str) -> None:
        service = self._service_for_user(user)
        try:
            if user.google_folder_id:
                metadata = service.files().get(fileId=file_id, fields="parents,trashed").execute()
                if user.google_folder_id not in metadata.get("parents", []):
                    raise PermissionError("File is outside the configured Drive folder")
            service.files().delete(fileId=file_id).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                raise FileNotFoundError(file_id) from exc
            raise
        logger.info("Deleted Drive file telegram_id=%s file_id=%s", user.telegram_id, file_id)

    def _resolve_or_create_folder_sync(self, user: User, folder_value: str) -> dict[str, str | bool]:
        service = self._service_for_user(user)
        folder_value = folder_value.strip()
        if not folder_value:
            raise DriveFolderError("Folder value cannot be empty")

        existing_by_id = self._get_folder_by_id(service, folder_value)
        if existing_by_id:
            return {**existing_by_id, "created": False}

        existing_by_name = self._find_folder_by_name(service, folder_value)
        if existing_by_name:
            return {**existing_by_name, "created": False}

        created = (
            service.files()
            .create(
                body={
                    "name": folder_value,
                    "mimeType": "application/vnd.google-apps.folder",
                },
                fields="id,name",
            )
            .execute()
        )
        logger.info(
            "Created Drive folder telegram_id=%s folder_name=%s folder_id=%s",
            user.telegram_id,
            folder_value,
            created["id"],
        )
        return {"id": created["id"], "name": created["name"], "created": True}

    def _get_folder_by_id(self, service, folder_id: str) -> dict[str, str] | None:
        try:
            folder = service.files().get(fileId=folder_id, fields="id,name,mimeType,trashed").execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                return None
            raise
        if folder.get("trashed"):
            raise DriveFolderError("That Drive folder is in trash")
        if folder.get("mimeType") != "application/vnd.google-apps.folder":
            raise DriveFolderError("That Drive ID is not a folder")
        return {"id": folder["id"], "name": folder["name"]}

    def _find_folder_by_name(self, service, folder_name: str) -> dict[str, str] | None:
        escaped_name = _escape_drive_query_string(folder_name)
        result = (
            service.files()
            .list(
                q=(
                    "mimeType = 'application/vnd.google-apps.folder' "
                    f"and name = '{escaped_name}' and trashed = false"
                ),
                pageSize=1,
                fields="files(id,name)",
            )
            .execute()
        )
        folders = result.get("files", [])
        return folders[0] if folders else None

    def _make_file_public(self, service, file_id: str, telegram_id: int) -> None:
        service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
            fields="id",
        ).execute()
        logger.info("Made Drive file public telegram_id=%s file_id=%s", telegram_id, file_id)

    def _folder_path(self, service, folder_id: str | None) -> str:
        if not folder_id:
            return "Drive root"
        try:
            folder = service.files().get(fileId=folder_id, fields="name").execute()
        except HttpError:
            logger.warning("Could not resolve Drive folder name folder_id=%s", folder_id)
            return f"Drive root / {folder_id}"
        return f"Drive root / {folder.get('name', folder_id)}"


def _escape_drive_query_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
