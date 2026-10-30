"""
Handlers and interactive UI for user reminder settings.
"""
import asyncio
import logging
from typing import Any, Dict, Optional
import warnings

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

warnings.filterwarnings("ignore", message=r".*per_message.*", category=PTBUserWarning)

from app.bot.keyboards.friend_menu import get_cancel_keyboard
from app.bot.keyboards.main_menu import get_main_menu_keyboard
from app.services.friend_ser import UserNotRegisteredError
from app.services.reminder_ser import (
    get_user_settings_dto,
    toggle_user_setting_dto,
    update_user_reminder_time_dto,
    update_user_timezone_dto,
)
from app.utils.timezone import (
    validate_reminder_time_format,
    validate_timezone_input,
)

logger = logging.getLogger(__name__)

# Conversation states for Settings updates
WAITING_FOR_SETTINGS_TIME = 10
WAITING_FOR_SETTINGS_TIMEZONE = 11

UNREGISTERED_SETTINGS_MESSAGE = (
    "⚠️ You are not registered yet. Please send /start to register before configuring settings."
)

CANCEL_SETTINGS_MESSAGE = "❌ Settings change cancelled. Returning to main menu."


def format_settings_panel_text(settings: Dict[str, Any]) -> str:
    """Format user settings into a clean, informative panel message."""
    master_status = "🔔 Enabled (Active)" if settings["is_enabled"] else "🔕 Disabled (All Paused)"
    t7 = "✅ Enabled" if settings["remind_7_days"] else "❌ Disabled"
    t1 = "✅ Enabled" if settings["remind_1_day"] else "❌ Disabled"
    t0 = "✅ Enabled" if settings["remind_same_day"] else "❌ Disabled"

    return (
        "⚙️ Birthday Reminder Settings\n\n"
        f"• All Reminders: {master_status}\n"
        f"• 7 Days Before: {t7}\n"
        f"• 1 Day Before: {t1}\n"
        f"• On Birthday: {t0}\n"
        f"• Notification Time: ⏰ {settings['reminder_time']} (24-hour format)\n"
        f"• Timezone: 🌍 {settings['timezone']}\n\n"
        "Tap a button below to toggle alerts or update your time and timezone:"
    )


def get_settings_inline_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    """Generate inline keyboard reflecting current user toggles."""
    master_btn = "🔕 Disable All" if settings["is_enabled"] else "🔔 Enable All"
    t7_btn = f"7 Days: {'✅ ON' if settings['remind_7_days'] else '❌ OFF'}"
    t1_btn = f"1 Day: {'✅ ON' if settings['remind_1_day'] else '❌ OFF'}"
    t0_btn = f"Today: {'✅ ON' if settings['remind_same_day'] else '❌ OFF'}"

    buttons = [
        [InlineKeyboardButton(master_btn, callback_data="settings_toggle_all")],
        [
            InlineKeyboardButton(t7_btn, callback_data="settings_toggle_7d"),
            InlineKeyboardButton(t1_btn, callback_data="settings_toggle_1d"),
        ],
        [InlineKeyboardButton(t0_btn, callback_data="settings_toggle_0d")],
        [
            InlineKeyboardButton("⏰ Change Time", callback_data="settings_prompt_time"),
            InlineKeyboardButton("🌍 Change Timezone", callback_data="settings_prompt_tz"),
        ],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="settings_back_main")],
    ]
    return InlineKeyboardMarkup(buttons)


def get_quick_timezone_keyboard() -> InlineKeyboardMarkup:
    """Provide quick inline buttons for popular timezones."""
    buttons = [
        [
            InlineKeyboardButton("Asia/Phnom_Penh", callback_data="tz_set:Asia/Phnom_Penh"),
            InlineKeyboardButton("Asia/Bangkok", callback_data="tz_set:Asia/Bangkok"),
        ],
        [
            InlineKeyboardButton("UTC", callback_data="tz_set:UTC"),
            InlineKeyboardButton("Asia/Singapore", callback_data="tz_set:Asia/Singapore"),
        ],
        [
            InlineKeyboardButton("America/New_York", callback_data="tz_set:America/New_York"),
            InlineKeyboardButton("Europe/London", callback_data="tz_set:Europe/London"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


async def open_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the Settings panel for the authenticated user."""
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
    if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict):
        engine = context.bot_data.get("db_engine")

    try:
        settings = await asyncio.to_thread(
            get_user_settings_dto, telegram_id=telegram_id, engine=engine
        )
    except UserNotRegisteredError:
        await update.message.reply_text(
            text=UNREGISTERED_SETTINGS_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
        return
    except Exception as err:
        logger.error("Failed to load settings for user %d: %s", telegram_id, err)
        await update.message.reply_text(
            text="⚠️ An unexpected error occurred while loading settings. Please try again later.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    await update.message.reply_text(
        text=format_settings_panel_text(settings),
        reply_markup=get_settings_inline_keyboard(settings),
    )


async def settings_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle interactive toggle buttons and navigation on the Settings panel."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    effective_user = update.effective_user
    if not effective_user:
        await query.answer("Could not identify user.")
        return

    telegram_id = int(effective_user.id)
    engine = None
    if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict):
        engine = context.bot_data.get("db_engine")

    data = query.data

    if data == "settings_back_main":
        await query.answer()
        if query.message:
            await query.message.reply_text(
                text="🏠 Main Menu",
                reply_markup=get_main_menu_keyboard(),
            )
        return

    # Map callback data to model field name
    field_map = {
        "settings_toggle_all": "is_enabled",
        "settings_toggle_7d": "remind_7_days",
        "settings_toggle_1d": "remind_1_day",
        "settings_toggle_0d": "remind_same_day",
    }

    if data in field_map:
        target_field = field_map[data]
        try:
            updated_settings = await asyncio.to_thread(
                toggle_user_setting_dto,
                telegram_id=telegram_id,
                field_name=target_field,
                engine=engine,
            )
            await query.answer("Setting updated!")
            await query.edit_message_text(
                text=format_settings_panel_text(updated_settings),
                reply_markup=get_settings_inline_keyboard(updated_settings),
            )
        except Exception as err:
            logger.error("Failed to toggle setting %s for user %d: %s", target_field, telegram_id, err)
            await query.answer("Failed to update setting. Please try again.")
    else:
        await query.answer("⚠️ Unrecognized option.")


# ==============================================================================
# Time and Timezone Conversation Handlers
# ==============================================================================


async def start_change_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Initiate the conversation flow to change reminder time."""
    target_msg = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()

    if target_msg:
        await target_msg.reply_text(
            text=(
                "⏰ Change Notification Time\n\n"
                "Please enter your preferred reminder time in 24-hour HH:MM format (e.g. 09:00 or 18:30):\n\n"
                "Send /cancel or tap ❌ Cancel to abort."
            ),
            reply_markup=get_cancel_keyboard(),
        )
    return WAITING_FOR_SETTINGS_TIME


async def process_change_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate and persist the entered reminder time."""
    if update.message is None:
        return WAITING_FOR_SETTINGS_TIME

    raw_text = (update.message.text or "").strip()
    if raw_text in {"❌ Cancel", "Cancel", "/cancel"}:
        return await cancel_settings_input(update, context)

    is_valid, cleaned_time, error_msg = validate_reminder_time_format(raw_text)
    if not is_valid:
        await update.message.reply_text(
            text=f"⚠️ {error_msg}",
            reply_markup=get_cancel_keyboard(),
        )
        return WAITING_FOR_SETTINGS_TIME

    effective_user = update.effective_user
    telegram_id = int(effective_user.id) if effective_user else 0
    engine = context.bot_data.get("db_engine") if hasattr(context, "bot_data") and isinstance(context.bot_data, dict) else None

    try:
        updated = await asyncio.to_thread(
            update_user_reminder_time_dto,
            telegram_id=telegram_id,
            raw_time=cleaned_time,
            engine=engine,
        )
    except Exception as err:
        logger.error("Failed to update time for user %d: %s", telegram_id, err)
        await update.message.reply_text(
            text="⚠️ An unexpected error occurred while saving your notification time.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        text=f"✅ Notification time successfully updated to {cleaned_time}!",
        reply_markup=get_main_menu_keyboard(),
    )
    # Display updated settings panel
    await update.message.reply_text(
        text=format_settings_panel_text(updated),
        reply_markup=get_settings_inline_keyboard(updated),
    )
    return ConversationHandler.END


async def start_change_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Initiate the conversation flow to change user timezone."""
    target_msg = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()

    if target_msg:
        await target_msg.reply_text(
            text=(
                "🌍 Change Timezone\n\n"
                "Please select one of the common timezones below, or type any valid IANA timezone name "
                "(e.g. Asia/Phnom_Penh, Asia/Bangkok, UTC, America/New_York):\n\n"
                "Send /cancel or tap ❌ Cancel to abort."
            ),
            reply_markup=get_quick_timezone_keyboard(),
        )
    return WAITING_FOR_SETTINGS_TIMEZONE


async def process_change_timezone_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate and persist timezone name entered via text."""
    if update.message is None:
        return WAITING_FOR_SETTINGS_TIMEZONE

    raw_text = (update.message.text or "").strip()
    if raw_text in {"❌ Cancel", "Cancel", "/cancel"}:
        return await cancel_settings_input(update, context)

    is_valid, cleaned_tz, error_msg = validate_timezone_input(raw_text)
    if not is_valid:
        await update.message.reply_text(
            text=f"⚠️ {error_msg}",
            reply_markup=get_cancel_keyboard(),
        )
        return WAITING_FOR_SETTINGS_TIMEZONE

    effective_user = update.effective_user
    telegram_id = int(effective_user.id) if effective_user else 0
    engine = context.bot_data.get("db_engine") if hasattr(context, "bot_data") and isinstance(context.bot_data, dict) else None

    try:
        updated = await asyncio.to_thread(
            update_user_timezone_dto,
            telegram_id=telegram_id,
            raw_tz=cleaned_tz,
            engine=engine,
        )
    except Exception as err:
        logger.error("Failed to update timezone for user %d: %s", telegram_id, err)
        await update.message.reply_text(
            text="⚠️ An unexpected error occurred while saving your timezone.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        text=f"✅ Timezone successfully updated to {cleaned_tz}!",
        reply_markup=get_main_menu_keyboard(),
    )
    await update.message.reply_text(
        text=format_settings_panel_text(updated),
        reply_markup=get_settings_inline_keyboard(updated),
    )
    return ConversationHandler.END


async def process_quick_timezone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle timezone selected via quick inline buttons."""
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END

    data = query.data
    if not data or not data.startswith("tz_set:"):
        await query.answer("⚠️ Invalid timezone selection.", show_alert=True)
        return ConversationHandler.END

    tz_name = data[len("tz_set:"):].strip()
    if not tz_name:
        await query.answer("⚠️ Invalid timezone selection.", show_alert=True)
        return ConversationHandler.END
    effective_user = update.effective_user
    telegram_id = int(effective_user.id) if effective_user else 0
    engine = context.bot_data.get("db_engine") if hasattr(context, "bot_data") and isinstance(context.bot_data, dict) else None

    try:
        updated = await asyncio.to_thread(
            update_user_timezone_dto,
            telegram_id=telegram_id,
            raw_tz=tz_name,
            engine=engine,
        )
        await query.answer(f"Timezone set to {tz_name}!")
        if query.message:
            await query.message.reply_text(
                text=f"✅ Timezone successfully updated to {tz_name}!",
                reply_markup=get_main_menu_keyboard(),
            )
            await query.message.reply_text(
                text=format_settings_panel_text(updated),
                reply_markup=get_settings_inline_keyboard(updated),
            )
    except Exception as err:
        logger.error("Failed to set quick timezone for user %d: %s", telegram_id, err)
        await query.answer("Failed to update timezone.")

    return ConversationHandler.END


async def cancel_settings_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel any active settings input conversation and restore main menu."""
    target_msg = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()

    if target_msg:
        await target_msg.reply_text(
            text=CANCEL_SETTINGS_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
    return ConversationHandler.END


def get_settings_conversation_handler() -> ConversationHandler:
    """Build and return ConversationHandler for interactive time and timezone changes."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=PTBUserWarning)
        return ConversationHandler(
            entry_points=[
                CallbackQueryHandler(start_change_time, pattern=r"^settings_prompt_time$"),
                CommandHandler("settime", start_change_time),
                CallbackQueryHandler(start_change_timezone, pattern=r"^settings_prompt_tz$"),
                CommandHandler("settimezone", start_change_timezone),
            ],
            states={
                WAITING_FOR_SETTINGS_TIME: [
                    MessageHandler(filters.Regex(r"^(❌ Cancel|Cancel|/cancel)$"), cancel_settings_input),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, process_change_time),
                ],
                WAITING_FOR_SETTINGS_TIMEZONE: [
                    CallbackQueryHandler(process_quick_timezone_callback, pattern=r"^tz_set:"),
                    MessageHandler(filters.Regex(r"^(❌ Cancel|Cancel|/cancel)$"), cancel_settings_input),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, process_change_timezone_text),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", cancel_settings_input),
                MessageHandler(filters.Regex(r"^(❌ Cancel|Cancel|/cancel)$"), cancel_settings_input),
                MessageHandler(
                    filters.Regex(r"^(🔙 Main Menu|🔙 Back to Main Menu|Main Menu|➕ Add Friend|👥 My Friends|🎂 Upcoming Birthdays|⚙️ Settings)$"),
                    cancel_settings_input,
                ),
            ],
            allow_reentry=True,
        )
