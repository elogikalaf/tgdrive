from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.database.sqlite import Database
from bot.services.google_drive import DriveAuthError, DriveFolderError, DriveNotConnectedError, GoogleDriveService
from bot.services.oauth_service import OAuthService


logger = logging.getLogger(__name__)


def register_command_handlers(
    app: Client,
    database: Database,
    oauth_service: OAuthService,
    drive_service: GoogleDriveService,
    allowed_telegram_ids: set[int] | None,
) -> None:
    def allowed(message: Message) -> bool:
        return bool(message.from_user) and (
            allowed_telegram_ids is None or message.from_user.id in allowed_telegram_ids
        )

    async def reject_if_needed(message: Message) -> bool:
        if allowed(message):
            return False
        logger.warning("Rejected command from unauthorized telegram_id=%s", message.from_user.id if message.from_user else None)
        await message.reply_text("This bot is private.")
        return True

    @app.on_message(filters.private & filters.command("start"))
    async def start(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        await message.reply_text(
            "Send me a Telegram file and I will upload it to Google Drive.\n\n"
            "Commands:\n"
            "/connect - connect Google Drive\n"
            "/disconnect - remove stored Google tokens\n"
            "/folder <folder_id|root> - set upload folder\n"
            "/files - show recent files\n"
            "/delete <file_id> - delete a Drive file"
        )

    @app.on_message(filters.private & filters.command("connect"))
    async def connect(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        try:
            url = await oauth_service.create_authorization_url(message.from_user.id)
        except Exception:
            logger.exception("Failed to create OAuth URL telegram_id=%s", message.from_user.id)
            await message.reply_text("Could not start Google login. Check the server logs.")
            return

        await message.reply_text(
            "Open this link to connect Google Drive:\n\n"
            f"{url}\n\n"
            "The link expires in 15 minutes."
        )

    @app.on_message(filters.private & filters.command("disconnect"))
    async def disconnect(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        await database.disconnect(message.from_user.id)
        await message.reply_text("Google Drive disconnected. Your folder setting was kept.")

    @app.on_message(filters.private & filters.command("folder"))
    async def folder(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            user = await database.get_user(message.from_user.id)
            folder_id = user.google_folder_id if user else None
            await message.reply_text(f"Current upload folder: `{folder_id or 'Drive root'}`")
            return

        folder_value = parts[1].strip()
        try:
            folder_info = await drive_service.set_upload_folder(message.from_user.id, folder_value)
        except DriveNotConnectedError:
            await message.reply_text("Google Drive is not connected. Run /connect first.")
            return
        except DriveAuthError as exc:
            await message.reply_text(str(exc))
            return
        except DriveFolderError as exc:
            await message.reply_text(str(exc))
            return
        except Exception:
            logger.exception("Failed to set Drive folder telegram_id=%s", message.from_user.id)
            await message.reply_text("Could not set the Drive folder. Check the server logs.")
            return

        created_text = "Created and selected" if folder_info["created"] else "Selected"
        await message.reply_text(
            f"{created_text} upload folder: `{folder_info['name']}`\n"
            f"Folder ID: `{folder_info['id'] or 'Drive root'}`"
        )

    @app.on_message(filters.private & filters.command("files"))
    async def files(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        try:
            drive_files = await drive_service.list_files(message.from_user.id, limit=10)
        except DriveNotConnectedError:
            await message.reply_text("Google Drive is not connected. Run /connect first.")
            return
        except DriveAuthError as exc:
            await message.reply_text(str(exc))
            return
        except Exception:
            logger.exception("Failed to list Drive files telegram_id=%s", message.from_user.id)
            await message.reply_text("Could not list files. Check the server logs.")
            return

        if not drive_files:
            await message.reply_text("No files found in the configured Drive folder.")
            return

        lines = ["Recent files:"]
        buttons: list[list[InlineKeyboardButton]] = []
        for index, item in enumerate(drive_files, start=1):
            name = item.get("name", "Untitled")
            file_id = item.get("id", "")
            size = _format_size(item.get("size"))
            download_link = item.get("downloadLink", "")
            view_link = item.get("webViewLink", "")
            folder_path = item.get("folderPath", "Drive root")
            lines.append(
                f"{index}. `{name}`\n"
                f"   Size: {size}\n"
                f"   Path: {folder_path}\n"
                f"   ID: `{file_id}`"
            )
            if file_id:
                row = [InlineKeyboardButton(f"Download {index}", url=download_link)]
                if view_link:
                    row.append(InlineKeyboardButton(f"View {index}", url=view_link))
                buttons.append(row)
                buttons.append([InlineKeyboardButton(f"Delete {index}: {name[:28]}", callback_data=f"delete:{file_id}")])

        await message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )

    @app.on_message(filters.private & filters.command("delete"))
    async def delete(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            await message.reply_text("Usage: /delete <google_drive_file_id>\n\nYou can also use /files and tap a delete button.")
            return
        await _delete_file(message, drive_service, message.from_user.id, parts[1].strip())

    @app.on_callback_query(filters.regex(r"^delete:"))
    async def delete_callback(_: Client, callback: CallbackQuery) -> None:
        if not callback.from_user or (
            allowed_telegram_ids is not None and callback.from_user.id not in allowed_telegram_ids
        ):
            await callback.answer("This bot is private.", show_alert=True)
            return
        file_id = (callback.data or "").split(":", 1)[1]
        try:
            await drive_service.delete_file(callback.from_user.id, file_id)
        except FileNotFoundError:
            await callback.answer("File was not found.", show_alert=True)
            return
        except PermissionError:
            await callback.answer("File is outside the configured folder.", show_alert=True)
            return
        except Exception:
            logger.exception("Inline delete failed telegram_id=%s file_id=%s", callback.from_user.id, file_id)
            await callback.answer("Delete failed.", show_alert=True)
            return
        await callback.answer("Deleted.")
        if callback.message:
            await callback.message.reply_text(f"Deleted Drive file `{file_id}`.")


async def _delete_file(message: Message, drive_service: GoogleDriveService, telegram_id: int, file_id: str) -> None:
    try:
        await drive_service.delete_file(telegram_id, file_id)
    except DriveNotConnectedError:
        await message.reply_text("Google Drive is not connected. Run /connect first.")
    except DriveAuthError as exc:
        await message.reply_text(str(exc))
    except FileNotFoundError:
        await message.reply_text("That Drive file was not found.")
    except PermissionError:
        await message.reply_text("That file is outside the configured Drive folder.")
    except Exception:
        logger.exception("Failed to delete Drive file telegram_id=%s file_id=%s", telegram_id, file_id)
        await message.reply_text("Could not delete the file. Check the server logs.")
    else:
        await message.reply_text(f"Deleted Drive file `{file_id}`.")


def _format_size(raw_size: object) -> str:
    if raw_size in (None, ""):
        return "unknown size"
    size = float(raw_size)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
