"""
Keyboards for the Create Wish workflow, approval, sending, editing, and history.
"""
from typing import Any, Dict, List, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.ai.prompts import SUPPORTED_STYLES


def get_wish_friend_selection_keyboard(
    friends: List[Dict[str, Any]],
    include_history: bool = False,
) -> InlineKeyboardMarkup:
    """
    Inline keyboard displaying the user's friends to select for wish generation,
    along with Wish History (optional) and Main Menu options.
    """
    buttons = []
    row = []
    for f in friends:
        name = f.get("name", "Friend")
        friend_id = f.get("id") or f.get("friend_id")
        row.append(
            InlineKeyboardButton(f"👤 {name}", callback_data=f"wish_select:{friend_id}")
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if include_history:
        buttons.append([InlineKeyboardButton("📜 Wish History", callback_data="wish_hist:1")])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="wish_cancel")])
    return InlineKeyboardMarkup(buttons)


def get_wish_draft_keyboard(
    wish_id: int, current_style: str = "Friendly"
) -> InlineKeyboardMarkup:
    """
    Inline keyboard displayed for a DRAFT wish:
    - ✏️ Edit Wish
    - ✅ Approve Wish
    - 🔄 Generate Another
    - 🎨 Change Style
    - 📜 Wish History
    - 🔙 Main Menu
    """
    buttons = [
        [
            InlineKeyboardButton("✏️ Edit Wish", callback_data=f"wish_edit:{wish_id}"),
            InlineKeyboardButton("✅ Approve Wish", callback_data=f"wish_approve:{wish_id}"),
        ],
        [
            InlineKeyboardButton(
                "🔄 Generate Another",
                callback_data=f"wish_regen:{wish_id}:{current_style}",
            ),
            InlineKeyboardButton(
                "🎨 Change Style",
                callback_data=f"wish_styles:{wish_id}",
            ),
        ],
        [
            InlineKeyboardButton("📜 Wish History", callback_data="wish_hist:1"),
            InlineKeyboardButton("🔙 Main Menu", callback_data="wish_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def get_wish_result_keyboard(
    friend_id: int, current_style: str = "Friendly", wish_id: Optional[int] = None
) -> InlineKeyboardMarkup:
    """
    Backward-compatible keyboard for wish results.
    If wish_id is given, returns the full draft workflow keyboard.
    """
    if wish_id is not None:
        return get_wish_draft_keyboard(wish_id, current_style)

    buttons = [
        [
            InlineKeyboardButton(
                "🔄 Generate Another",
                callback_data=f"wish_regen:{friend_id}:{current_style}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎨 Change Style",
                callback_data=f"wish_styles:{friend_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔙 Main Menu",
                callback_data="wish_cancel",
            ),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def get_wish_approved_keyboard(wish_id: int) -> InlineKeyboardMarkup:
    """
    Inline keyboard displayed for an APPROVED wish:
    - 📤 Send Wish
    - 📋 Copy Wish
    - ✏️ Edit Again
    - 📜 Wish History
    - 🔙 Main Menu
    """
    buttons = [
        [
            InlineKeyboardButton("📤 Send Wish", callback_data=f"wish_send:{wish_id}"),
            InlineKeyboardButton("📋 Copy Wish", callback_data=f"wish_copy:{wish_id}"),
        ],
        [
            InlineKeyboardButton("✏️ Edit Again", callback_data=f"wish_edit:{wish_id}"),
            InlineKeyboardButton("📜 Wish History", callback_data="wish_hist:1"),
        ],
        [
            InlineKeyboardButton("🔙 Main Menu", callback_data="wish_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def get_wish_sent_keyboard(wish_id: int) -> InlineKeyboardMarkup:
    """
    Inline keyboard displayed after a wish has been successfully sent:
    - 📋 Copy Wish
    - ✏️ Edit as New
    - 📜 Wish History
    - 🔙 Main Menu
    """
    buttons = [
        [
            InlineKeyboardButton("📋 Copy Wish", callback_data=f"wish_copy:{wish_id}"),
            InlineKeyboardButton("✏️ Edit as New", callback_data=f"wish_edit:{wish_id}"),
        ],
        [
            InlineKeyboardButton("📜 Wish History", callback_data="wish_hist:1"),
            InlineKeyboardButton("🔙 Main Menu", callback_data="wish_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def get_wish_styles_keyboard(
    id_val: int, is_wish_id: bool = True
) -> InlineKeyboardMarkup:
    """
    Inline keyboard offering supported birthday wish style presets.
    """
    styles = list(SUPPORTED_STYLES.keys())
    buttons = []
    row = []

    for s in styles:
        row.append(
            InlineKeyboardButton(s, callback_data=f"wish_setstyle:{id_val}:{s}")
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    back_cb = f"wish_view:{id_val}" if is_wish_id else f"wish_select:{id_val}"
    buttons.append(
        [InlineKeyboardButton("🔙 Back to Wish", callback_data=back_cb)]
    )
    return InlineKeyboardMarkup(buttons)


def get_wish_cancel_edit_keyboard(wish_id: int) -> InlineKeyboardMarkup:
    """
    Inline keyboard provided when entering text edit mode, allowing cancellation.
    """
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel Edit", callback_data=f"wish_edit_cancel:{wish_id}")]]
    )


def get_copy_view_keyboard(wish_id: int) -> InlineKeyboardMarkup:
    """
    Inline keyboard shown on the copy wish view screen.
    """
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔙 Back to Wish", callback_data=f"wish_view:{wish_id}")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="wish_cancel")],
        ]
    )


def get_wish_history_keyboard(
    items: List[Dict[str, Any]], current_page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """
    Inline keyboard displaying paginated wish history list and controls.
    """
    buttons = []

    for item in items:
        name = item.get("friend_name", "Friend")
        status = item.get("status", "DRAFT")
        created_at = item.get("created_at")
        date_str = created_at.strftime("%b %d") if created_at else ""

        status_icon = "📝"
        if status == "APPROVED":
            status_icon = "✅"
        elif status == "SENT":
            status_icon = "🚀"

        btn_text = f"👤 {name} - {date_str} ({status_icon})"
        wish_id = item.get("wish_id") or item.get("id")
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"wish_view:{wish_id}")])

    # Pagination navigation controls
    if total_pages > 1:
        nav_row = []
        if current_page > 1:
            nav_row.append(
                InlineKeyboardButton("⬅️ Prev", callback_data=f"wish_hist:{current_page - 1}")
            )
        nav_row.append(
            InlineKeyboardButton(f"Page {current_page}/{total_pages}", callback_data="wish_noop")
        )
        if current_page < total_pages:
            nav_row.append(
                InlineKeyboardButton("Next ➡️", callback_data=f"wish_hist:{current_page + 1}")
            )
        buttons.append(nav_row)

    buttons.append(
        [
            InlineKeyboardButton("✨ Create Wish", callback_data="wish_new"),
            InlineKeyboardButton("🔙 Main Menu", callback_data="wish_cancel"),
        ]
    )
    return InlineKeyboardMarkup(buttons)


def get_wish_error_keyboard(
    retry_id: Optional[int] = None, is_wish_id: bool = False
) -> InlineKeyboardMarkup:
    """
    Inline keyboard displayed when an AI error or timeout occurs.
    """
    buttons = []
    if retry_id is not None:
        cb = f"wish_view:{retry_id}" if is_wish_id else f"wish_select:{retry_id}"
        buttons.append(
            [InlineKeyboardButton("🔄 Try Again", callback_data=cb)]
        )
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="wish_cancel")])
    return InlineKeyboardMarkup(buttons)
