from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.services.google_drive import DriveAuthError, DriveNotConnectedError, GoogleDriveService


logger = logging.getLogger(__name__)

MEDIA_FILTER = (
    filters.document
    | filters.video
    | filters.audio
    | filters.voice
    | filters.video_note
    | filters.animation
    | filters.photo
    | filters.sticker
)


def register_media_handlers(
    app: Client,
    drive_service: GoogleDriveService,
    download_dir: Path,
    allowed_telegram_ids: set[int] | None,
) -> None:
    def allowed(message: Message) -> bool:
        return bool(message.from_user) and (
            allowed_telegram_ids is None or message.from_user.id in allowed_telegram_ids
        )

    @app.on_message(filters.private & MEDIA_FILTER)
    async def handle_media(_: Client, message: Message) -> None:
        if not allowed(message):
            logger.warning("Rejected media from unauthorized telegram_id=%s", message.from_user.id if message.from_user else None)
            await message.reply_text("This bot is private.")
            return
        assert message.from_user is not None

        status = await message.reply_text("Downloading from Telegram...")
        local_path: Path | None = None
        drive_name = _drive_name(message)
        target_path = download_dir / _local_name(message, drive_name)

        try:
            downloaded = await message.download(
                file_name=str(target_path),
                progress=_download_progress,
                progress_args=(message.from_user.id, drive_name),
            )
            if not downloaded:
                raise RuntimeError("Telegram download returned no file path")
            local_path = Path(downloaded)
            await status.edit_text("Uploading to Google Drive...")
            uploaded = await drive_service.upload_file(message.from_user.id, local_path, drive_name)
        except DriveNotConnectedError:
            await status.edit_text("Google Drive is not connected. Run /connect first.")
            return
        except DriveAuthError as exc:
            await status.edit_text(str(exc))
            return
        except Exception:
            logger.exception("Media upload failed telegram_id=%s", message.from_user.id)
            await status.edit_text("Upload failed. Check the server logs.")
            return
        finally:
            if local_path and local_path.exists():
                try:
                    local_path.unlink()
                    logger.info("Deleted temporary file path=%s", local_path)
                except OSError:
                    logger.exception("Failed to delete temporary file path=%s", local_path)

        text = f"Uploaded `{uploaded.get('name', drive_name)}` to Google Drive."
        if uploaded.get("downloadLink"):
            text += f"\n\nDownload:\n{uploaded['downloadLink']}"
        if uploaded.get("webViewLink"):
            text += f"\n\nView:\n{uploaded['webViewLink']}"
        await status.edit_text(text)


def _download_progress(current: int, total: int, telegram_id: int, file_name: str) -> None:
    if total <= 0:
        return
    progress = int(current * 100 / total)
    if progress % 10 == 0:
        logger.info(
            "Telegram download progress telegram_id=%s file=%s progress=%s%%",
            telegram_id,
            file_name,
            progress,
        )


def _drive_name(message: Message) -> str:
    media = (
        message.document
        or message.video
        or message.audio
        or message.voice
        or message.video_note
        or message.animation
        or message.photo
        or message.sticker
    )
    original = getattr(media, "file_name", None)
    if original:
        return _sanitize_filename(original)

    extension = ""
    if message.voice:
        extension = ".ogg"
    elif message.video_note:
        extension = ".mp4"
    elif message.photo:
        extension = ".jpg"
    elif message.animation:
        extension = ".mp4"
    elif message.sticker:
        if getattr(message.sticker, "is_animated", False):
            extension = ".tgs"
        elif getattr(message.sticker, "is_video", False):
            extension = ".webm"
        else:
            extension = ".webp"
    return f"telegram_{message.chat.id}_{message.id}{extension}"


def _local_name(message: Message, drive_name: str) -> str:
    prefix = f"{message.from_user.id if message.from_user else 'unknown'}_{message.id}_{int(time.time())}"
    return f"{prefix}_{drive_name}"


def _sanitize_filename(filename: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._ -]", "_", filename).strip(" .")
    return sanitized or "telegram_file"
