"""
Handlers for the Upcoming Birthdays feature.
"""
import asyncio
import logging
from typing import Any, Dict, List
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.keyboards.main_menu import get_main_menu_keyboard
from app.services.birthday_ser import handle_get_upcoming_birthdays
from app.services.friend_ser import UserNotRegisteredError

logger = logging.getLogger(__name__)

NO_FRIENDS_MESSAGE = (
    "🎂 Upcoming Birthdays\n\n"
    "You haven't added any friends yet!\n"
    "Tap \"➕ Add Friend\" below to add your friends and track their birthdays."
)

NO_UPCOMING_MESSAGE = (
    "🎂 Upcoming Birthdays\n\n"
    "No upcoming birthdays in the next 7 days.\n"
    "All your celebrations are further away!\n\n"
    "💡 Tip: Tap \"👥 My Friends\" to view your full friend list."
)

UNREGISTERED_MESSAGE = (
    "⚠️ You are not registered yet. Please send /start to register before viewing upcoming birthdays."
)

ERROR_MESSAGE = (
    "⚠️ An unexpected error occurred while retrieving upcoming birthdays. Please try again later."
)


def format_upcoming_birthdays_message(upcoming: List[Dict[str, Any]]) -> str:
    """
    Format a list of upcoming birthdays into a grouped, readable message.

    Groups:
        - 🎉 Today (days_until == 0)
        - ⏰ Tomorrow (days_until == 1)
        - 🗓️ Coming Up (days_until >= 2)
    """
    today_items = [u for u in upcoming if u["days_until"] == 0]
    tomorrow_items = [u for u in upcoming if u["days_until"] == 1]
    later_items = [u for u in upcoming if u["days_until"] > 1]

    sections: List[str] = ["🎂 Upcoming Birthdays (Next 7 Days)\n"]

    if today_items:
        sections.append("🎉 Today:")
        for item in today_items:
            age_str = (
                f" (Turning {item['turning_age']})"
                if item["turning_age"] is not None
                else ""
            )
            sections.append(f"• {item['name']} — {item['date_display']}{age_str}")
        sections.append("")

    if tomorrow_items:
        sections.append("⏰ Tomorrow:")
        for item in tomorrow_items:
            age_str = (
                f" (Turning {item['turning_age']})"
                if item["turning_age"] is not None
                else ""
            )
            sections.append(f"• {item['name']} — {item['date_display']}{age_str}")
        sections.append("")

    if later_items:
        sections.append("🗓️ Coming Up:")
        for item in later_items:
            age_str = (
                f", Turning {item['turning_age']}"
                if item["turning_age"] is not None
                else ""
            )
            day_word = "days" if item["days_until"] != 1 else "day"
            sections.append(
                f"• {item['name']} — {item['date_display']} (In {item['days_until']} {day_word}{age_str})"
            )
        sections.append("")

    return "\n".join(sections).strip()


async def upcoming_birthdays_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle requests to view upcoming birthdays within the next 7 calendar days.
    Triggered by tapping '🎂 Upcoming Birthdays' or issuing /upcoming or /birthdays.
    """
    if update.message is None:
        return

    effective_user = update.effective_user
    if not effective_user:
        await update.message.reply_text(
            text="⚠️ Could not identify your Telegram account. Please try again.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    telegram_id = int(effective_user.id)
    engine = None
    ref_date = None
    if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict):
        engine = context.bot_data.get("db_engine")
        ref_date = context.bot_data.get("reference_date")

    try:
        has_friends, upcoming = await asyncio.to_thread(
            handle_get_upcoming_birthdays,
            telegram_id=telegram_id,
            days=7,
            engine=engine,
            reference_date=ref_date,
        )
    except UserNotRegisteredError:
        logger.warning(
            "Unregistered user %d attempted to view upcoming birthdays", telegram_id
        )
        await update.message.reply_text(
            text=UNREGISTERED_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
        return
    except Exception as error:
        logger.error(
            "Failed to retrieve upcoming birthdays for user %d: %s",
            telegram_id,
            error,
        )
        await update.message.reply_text(
            text=ERROR_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
        return

    if not has_friends:
        await update.message.reply_text(
            text=NO_FRIENDS_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
    elif not upcoming:
        await update.message.reply_text(
            text=NO_UPCOMING_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
    else:
        text = format_upcoming_birthdays_message(upcoming)
        await update.message.reply_text(
            text=text,
            reply_markup=get_main_menu_keyboard(),
        )
