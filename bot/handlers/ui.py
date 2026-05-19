from __future__ import annotations

from typing import Any

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from pyrogram.enums import ButtonStyle
except ImportError:  # Older Pyrogram/Pyrofork builds do not expose styled buttons.
    ButtonStyle = None  # type: ignore[assignment]


def action_button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: Any = None,
) -> InlineKeyboardButton:
    kwargs: dict[str, Any] = {"text": text}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    if style is not None:
        kwargs["style"] = style
    try:
        return InlineKeyboardButton(**kwargs)
    except TypeError:
        kwargs.pop("style", None)
        return InlineKeyboardButton(**kwargs)


def menu_keyboard(*, connected: bool | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if connected is False:
        rows.append([action_button("Connect Google Drive", callback_data="menu:connect", style=_style("PRIMARY"))])
    elif connected is True:
        rows.append([action_button("Recent Files", callback_data="menu:files", style=_style("PRIMARY"))])
        rows.append(
            [
                action_button("Status", callback_data="menu:status", style=_style("SUCCESS")),
                action_button("Folder", callback_data="menu:folder", style=_style("PRIMARY")),
            ]
        )
        rows.append([action_button("Disconnect", callback_data="menu:disconnect", style=_style("DANGER"))])
    else:
        rows.append(
            [
                action_button("Connect", callback_data="menu:connect", style=_style("PRIMARY")),
                action_button("Status", callback_data="menu:status", style=_style("SUCCESS")),
            ]
        )
        rows.append(
            [
                action_button("Recent Files", callback_data="menu:files", style=_style("PRIMARY")),
                action_button("Folder", callback_data="menu:folder", style=_style("PRIMARY")),
            ]
        )
    return InlineKeyboardMarkup(rows)


def button_style(name: str) -> Any:
    return _style(name)


def _style(name: str) -> Any:
    return getattr(ButtonStyle, name, None) if ButtonStyle else None
