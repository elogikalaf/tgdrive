from __future__ import annotations

import logging
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.database.sqlite import Database
from bot.handlers.ui import action_button, button_style, menu_keyboard
from bot.services.google_drive import DriveAuthError, DriveFolderError, DriveNotConnectedError, GoogleDriveService
from bot.services.oauth_service import OAuthService


logger = logging.getLogger(__name__)


def register_command_handlers(
    app: Client,
    database: Database,
    oauth_service: OAuthService,
    drive_service: GoogleDriveService,
    download_dir: Path,
    allowed_telegram_ids: set[int] | None,
) -> None:
    def allowed(message: Message) -> bool:
        return bool(message.from_user) and (
            allowed_telegram_ids is None or message.from_user.id in allowed_telegram_ids
        )

    def callback_allowed(callback: CallbackQuery) -> bool:
        return bool(callback.from_user) and (
            allowed_telegram_ids is None or callback.from_user.id in allowed_telegram_ids
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
        assert message.from_user is not None
        await message.reply_text(
            "Send me a Telegram file and I will upload it to Google Drive.\n\n"
            "Use the buttons below for common actions, or keep using commands:\n\n"
            "Commands:\n"
            "/connect - connect Google Drive\n"
            "/disconnect - remove stored Google tokens\n"
            "/folder <folder_id|folder_name|root> - set upload folder\n"
            "/files - show recent files\n"
            "/status - show connection and storage status\n"
            "/public <file_id> - make a Drive file public\n"
            "/private <file_id> - remove public sharing from a Drive file\n"
            "/delete <file_id> - delete a Drive file",
            reply_markup=menu_keyboard(connected=await _is_connected(drive_service, message.from_user.id)),
        )

    @app.on_message(filters.private & filters.command("connect"))
    async def connect(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        await _send_connect(message, oauth_service, message.from_user.id)

    @app.on_message(filters.private & filters.command("disconnect"))
    async def disconnect(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        await _disconnect(message, database, message.from_user.id)

    @app.on_message(filters.private & filters.command("folder"))
    async def folder(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            await _send_folder_help(message, database, message.from_user.id)
            return

        folder_value = parts[1].strip()
        try:
            folder_info = await drive_service.set_upload_folder(message.from_user.id, folder_value)
        except DriveNotConnectedError:
            await message.reply_text("Google Drive is not connected. Run /connect first.", reply_markup=menu_keyboard(connected=False))
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
            f"Folder ID: `{folder_info['id'] or 'Drive root'}`",
            reply_markup=menu_keyboard(connected=True),
        )

    @app.on_message(filters.private & filters.command("files"))
    async def files(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        await _send_files(message, drive_service, message.from_user.id)

    @app.on_message(filters.private & filters.command("status"))
    async def status(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        await _send_status(message, drive_service, download_dir, message.from_user.id)

    @app.on_message(filters.private & filters.command("public"))
    async def public(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        file_id = _command_argument(message)
        if not file_id:
            await message.reply_text("Usage: /public <google_drive_file_id>\n\nYou can also use /files and tap a Public button.")
            return
        await _make_public(message, drive_service, message.from_user.id, file_id)

    @app.on_message(filters.private & filters.command("private"))
    async def private(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        file_id = _command_argument(message)
        if not file_id:
            await message.reply_text("Usage: /private <google_drive_file_id>\n\nYou can also use /files and tap a Private button.")
            return
        await _make_private(message, drive_service, message.from_user.id, file_id)

    @app.on_message(filters.private & filters.command("delete"))
    async def delete(_: Client, message: Message) -> None:
        if await reject_if_needed(message):
            return
        assert message.from_user is not None
        file_id = _command_argument(message)
        if not file_id:
            await message.reply_text("Usage: /delete <google_drive_file_id>\n\nYou can also use /files and tap a Delete button.")
            return
        await _delete_file(message, drive_service, message.from_user.id, file_id)

    @app.on_callback_query(filters.regex(r"^menu:"))
    async def menu_callback(_: Client, callback: CallbackQuery) -> None:
        if not callback_allowed(callback):
            await callback.answer("This bot is private.", show_alert=True)
            return
        assert callback.from_user is not None
        if not callback.message:
            await callback.answer()
            return
        await callback.answer()
        action = (callback.data or "").split(":", 1)[1]
        if action == "connect":
            await _send_connect(callback.message, oauth_service, callback.from_user.id)
        elif action == "disconnect":
            await _disconnect(callback.message, database, callback.from_user.id)
        elif action == "folder":
            await _send_folder_help(callback.message, database, callback.from_user.id)
        elif action == "files":
            await _send_files(callback.message, drive_service, callback.from_user.id)
        elif action == "status":
            await _send_status(callback.message, drive_service, download_dir, callback.from_user.id)

    @app.on_callback_query(filters.regex(r"^share:"))
    async def share_callback(_: Client, callback: CallbackQuery) -> None:
        if not callback_allowed(callback):
            await callback.answer("This bot is private.", show_alert=True)
            return
        assert callback.from_user is not None
        parts = (callback.data or "").split(":", 2)
        if len(parts) != 3:
            await callback.answer("Invalid action.", show_alert=True)
            return
        await callback.answer()
        action, file_id = parts[1], parts[2]
        if callback.message and action == "public":
            await _make_public(callback.message, drive_service, callback.from_user.id, file_id)
        elif callback.message and action == "private":
            await _make_private(callback.message, drive_service, callback.from_user.id, file_id)

    @app.on_callback_query(filters.regex(r"^delete:"))
    async def delete_callback(_: Client, callback: CallbackQuery) -> None:
        if not callback_allowed(callback):
            await callback.answer("This bot is private.", show_alert=True)
            return
        assert callback.from_user is not None
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
            await callback.message.reply_text(f"Deleted Drive file `{file_id}`.", reply_markup=menu_keyboard(connected=True))


async def _send_connect(message: Message, oauth_service: OAuthService, telegram_id: int) -> None:
    try:
        url = await oauth_service.create_authorization_url(telegram_id)
    except Exception:
        logger.exception("Failed to create OAuth URL telegram_id=%s", telegram_id)
        await message.reply_text("Could not start Google login. Check the server logs.")
        return

    await message.reply_text(
        "Open this link to connect Google Drive. The link expires in 15 minutes.",
        reply_markup=InlineKeyboardMarkup([[action_button("Open Google Login", url=url, style=button_style("PRIMARY"))]]),
    )


async def _disconnect(message: Message, database: Database, telegram_id: int) -> None:
    await database.disconnect(telegram_id)
    await message.reply_text("Google Drive disconnected. Your folder setting was kept.", reply_markup=menu_keyboard(connected=False))


async def _send_folder_help(message: Message, database: Database, telegram_id: int) -> None:
    user = await database.get_user(telegram_id)
    folder_id = user.google_folder_id if user else None
    await message.reply_text(
        f"Current upload folder: `{folder_id or 'Drive root'}`\n\n"
        "To change it, send `/folder <folder_id|folder_name|root>`.",
        reply_markup=menu_keyboard(connected=True),
    )


async def _send_files(message: Message, drive_service: GoogleDriveService, telegram_id: int) -> None:
    try:
        drive_files = await drive_service.list_files(telegram_id, limit=10)
    except DriveNotConnectedError:
        await message.reply_text("Google Drive is not connected. Run /connect first.", reply_markup=menu_keyboard(connected=False))
        return
    except DriveAuthError as exc:
        await message.reply_text(str(exc))
        return
    except Exception:
        logger.exception("Failed to list Drive files telegram_id=%s", telegram_id)
        await message.reply_text("Could not list files. Check the server logs.")
        return

    if not drive_files:
        await message.reply_text("No files found in the configured Drive folder.", reply_markup=menu_keyboard(connected=True))
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
        sharing_state = item.get("sharingState", "Unknown")
        lines.append(
            f"{index}. `{name}`\n"
            f"   Size: {size}\n"
            f"   Path: {folder_path}\n"
            f"   Sharing: {sharing_state}\n"
            f"   ID: `{file_id}`"
        )
        if file_id:
            row: list[InlineKeyboardButton] = []
            if download_link:
                row.append(action_button(f"Download {index}", url=download_link, style=button_style("SUCCESS")))
            if view_link:
                row.append(action_button(f"View {index}", url=view_link, style=button_style("PRIMARY")))
            if row:
                buttons.append(row)
            buttons.append(
                [
                    action_button(f"Public {index}", callback_data=f"share:public:{file_id}", style=button_style("SUCCESS")),
                    action_button(f"Private {index}", callback_data=f"share:private:{file_id}", style=button_style("PRIMARY")),
                ]
            )
            buttons.append([action_button(f"Delete {index}: {name[:28]}", callback_data=f"delete:{file_id}", style=button_style("DANGER"))])
    buttons.append([action_button("Refresh", callback_data="menu:files", style=button_style("PRIMARY"))])

    await message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def _send_status(
    message: Message,
    drive_service: GoogleDriveService,
    download_dir: Path,
    telegram_id: int,
) -> None:
    try:
        drive_status = await drive_service.get_status(telegram_id)
    except DriveAuthError as exc:
        await message.reply_text(str(exc))
        return
    except Exception:
        logger.exception("Failed to build status telegram_id=%s", telegram_id)
        await message.reply_text("Could not load status. Check the server logs.")
        return

    temp_usage = _directory_usage(download_dir)
    lines = [
        "Status",
        f"Google Drive: {'connected' if drive_status['connected'] else 'not connected'}",
        f"Upload path: {drive_status['folderPath']}",
    ]

    account = drive_status.get("account")
    if account:
        account_name = account.get("displayName") or "Unknown"
        account_email = account.get("emailAddress") or "email unavailable"
        lines.append(f"Google account: {account_name} <{account_email}>")

    quota = drive_status.get("quota")
    if quota:
        lines.extend(_format_quota_lines(quota))
    elif drive_status["connected"]:
        lines.append("Google storage: unavailable with current API response")

    lines.append(f"Temp downloads: {_format_size(temp_usage['bytes'])} in {temp_usage['files']} files")
    await message.reply_text("\n".join(lines), reply_markup=menu_keyboard(connected=drive_status["connected"]))


async def _make_public(message: Message, drive_service: GoogleDriveService, telegram_id: int, file_id: str) -> None:
    try:
        download_link = await drive_service.make_public(telegram_id, file_id)
    except DriveNotConnectedError:
        await message.reply_text("Google Drive is not connected. Run /connect first.", reply_markup=menu_keyboard(connected=False))
    except DriveAuthError as exc:
        await message.reply_text(str(exc))
    except FileNotFoundError:
        await message.reply_text("That Drive file was not found.")
    except Exception:
        logger.exception("Failed to make Drive file public telegram_id=%s file_id=%s", telegram_id, file_id)
        await message.reply_text("Could not make the file public. Check the server logs.")
    else:
        await message.reply_text(f"File is public.\n\nDownload:\n{download_link}", reply_markup=menu_keyboard(connected=True))


async def _make_private(message: Message, drive_service: GoogleDriveService, telegram_id: int, file_id: str) -> None:
    try:
        await drive_service.make_private(telegram_id, file_id)
    except DriveNotConnectedError:
        await message.reply_text("Google Drive is not connected. Run /connect first.", reply_markup=menu_keyboard(connected=False))
    except DriveAuthError as exc:
        await message.reply_text(str(exc))
    except FileNotFoundError:
        await message.reply_text("That Drive file was not found.")
    except Exception:
        logger.exception("Failed to make Drive file private telegram_id=%s file_id=%s", telegram_id, file_id)
        await message.reply_text("Could not make the file private. Check the server logs.")
    else:
        await message.reply_text(f"Public sharing removed for `{file_id}`.", reply_markup=menu_keyboard(connected=True))


async def _delete_file(message: Message, drive_service: GoogleDriveService, telegram_id: int, file_id: str) -> None:
    try:
        await drive_service.delete_file(telegram_id, file_id)
    except DriveNotConnectedError:
        await message.reply_text("Google Drive is not connected. Run /connect first.", reply_markup=menu_keyboard(connected=False))
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
        await message.reply_text(f"Deleted Drive file `{file_id}`.", reply_markup=menu_keyboard(connected=True))


async def _is_connected(drive_service: GoogleDriveService, telegram_id: int) -> bool | None:
    try:
        status = await drive_service.get_status(telegram_id)
    except Exception:
        return None
    return bool(status["connected"])


def _format_size(raw_size: object) -> str:
    if raw_size in (None, ""):
        return "unknown size"
    size = float(raw_size)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _command_argument(message: Message) -> str | None:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 and parts[1].strip() else None


def _directory_usage(path: Path) -> dict[str, int]:
    total_bytes = 0
    total_files = 0
    if not path.exists():
        return {"bytes": 0, "files": 0}
    for item in path.rglob("*"):
        if item.is_file():
            total_files += 1
            total_bytes += item.stat().st_size
    return {"bytes": total_bytes, "files": total_files}


def _format_quota_lines(quota: dict[str, str]) -> list[str]:
    usage = int(quota.get("usage", 0))
    usage_in_drive = int(quota.get("usageInDrive", 0))
    trash = int(quota.get("usageInDriveTrash", 0))
    raw_limit = quota.get("limit")
    if raw_limit:
        limit = int(raw_limit)
        free = max(limit - usage, 0)
        return [
            f"Google storage: {_format_size(usage)} used / {_format_size(limit)} total",
            f"Google free: {_format_size(free)}",
            f"Drive files: {_format_size(usage_in_drive)} used, {_format_size(trash)} in trash",
        ]
    return [
        f"Google storage: {_format_size(usage)} used",
        "Google free: unlimited or unavailable",
        f"Drive files: {_format_size(usage_in_drive)} used, {_format_size(trash)} in trash",
    ]
