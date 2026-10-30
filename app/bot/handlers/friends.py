"""
Handlers for friend management, search, and friend profile/preference flows.
"""
import asyncio
import logging
import warnings
from typing import Any, Dict, List, Optional
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

from app.bot.keyboards.friend_menu import (
    get_cancel_keyboard,
    get_friend_list_keyboard,
    get_friend_profile_inline_keyboard,
    get_friends_inline_keyboard,
    get_relationship_presets_inline_keyboard,
    get_wish_style_presets_inline_keyboard,
)
from app.bot.keyboards.main_menu import get_main_menu_keyboard
from app.bot.states.friend_states import (
    WAITING_FOR_BIRTHDAY,
    WAITING_FOR_NAME,
    WAITING_FOR_PROFILE_INTERESTS,
    WAITING_FOR_PROFILE_NOTES,
    WAITING_FOR_PROFILE_RELATIONSHIP,
    WAITING_FOR_PROFILE_WISH_STYLE,
    WAITING_FOR_SEARCH_QUERY,
)
from app.services.friend_ser import (
    FriendNotFoundError,
    UserNotRegisteredError,
    handle_add_friend,
    handle_clear_friend_profile,
    handle_get_friend_profile,
    handle_get_friends,
    handle_search_friends,
    handle_update_friend_profile,
)
from app.utils.validators import (
    validate_and_parse_birthday,
    validate_friend_name,
    validate_interests,
    validate_notes,
    validate_relationship,
    validate_wish_style,
)
from app.utils.callback_utils import (
    parse_callback_id,
    parse_callback_id_and_value,
)

logger = logging.getLogger(__name__)

# User-facing prompt and message strings
PROMPT_NAME_MESSAGE = (
    "➕ Add a Friend\n\n"
    "Please enter your friend's name:"
)

CANCEL_MESSAGE = (
    "❌ Friend addition cancelled. Returning to the main menu."
)

UNREGISTERED_ERROR_MESSAGE = (
    "⚠️ You are not registered yet. Please send /start to register before using this feature."
)

SAVE_ERROR_MESSAGE = (
    "⚠️ An unexpected error occurred while saving your friend. Please try again later."
)

EMPTY_FRIENDS_MESSAGE = (
    "👥 My Friends\n\n"
    "You haven't added any friends yet!\n"
    "Tap \"➕ Add Friend\" below to add your first friend."
)

RETRIEVAL_ERROR_MESSAGE = (
    "⚠️ An unexpected error occurred while retrieving your friends. Please try again later."
)

SEARCH_PROMPT_MESSAGE = (
    "🔍 Search Friends\n\n"
    "Please enter the name of the friend you want to search for:"
)

SEARCH_CANCEL_MESSAGE = (
    "❌ Search cancelled. Returning to friends list."
)

SEARCH_ERROR_MESSAGE = (
    "⚠️ An unexpected error occurred while searching your friends. Please try again later."
)

CANCEL_PROFILE_MESSAGE = "❌ Profile update cancelled. Returning to main menu."


def format_friends_list_message(friends: List[Dict[str, Any]]) -> str:
    """Format list of friends into a clean, readable message."""
    if not friends:
        return EMPTY_FRIENDS_MESSAGE
    lines = [f"👥 My Friends ({len(friends)})\n"]
    for item in friends:
        lines.append(f"• {item['name']} — {item['display_birthday']}")
    lines.append("\n💡 Tip: Tap \"🔍 Search Friend\" or send /profile <name> to manage a friend's profile details.")
    return "\n".join(lines)


def format_search_results_message(query: str, results: List[Dict[str, Any]]) -> str:
    """Format search query results into a readable message."""
    if not results:
        return f"🔍 No friends found matching \"{query}\"."
    lines = [f"🔍 Search results for \"{query}\" ({len(results)}):\n"]
    for item in results:
        lines.append(f"• {item['name']} — {item['display_birthday']}")
    lines.append("\n💡 Send /profile <name> to view or edit their profile preferences.")
    return "\n".join(lines)


def format_friend_profile_message(profile: Dict[str, Any]) -> str:
    """Format friend profile details and preferences into a clean user-facing panel."""
    name = profile.get("name", "Friend")
    birthday = profile.get("display_birthday", "Unknown")
    rel = profile.get("relationship") or "Not set"
    interests = profile.get("interests") or "Not set"
    notes = profile.get("notes") or "Not set"
    style = profile.get("wish_style") or "Not set"

    return (
        f"👤 Friend Profile: {name}\n"
        f"🎂 Birthday: {birthday}\n\n"
        f"• 👥 Relationship: {rel}\n"
        f"• 🎨 Interests & Hobbies: {interests}\n"
        f"• 📝 Personal Notes: {notes}\n"
        f"• ✨ Wish Style: {style}\n\n"
        "Tap a button below to update any field or clear the profile:"
    )


async def start_add_friend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Start the guided Add Friend conversation flow.
    Invoked when a user taps '➕ Add Friend' or sends /addfriend.
    """
    if update.message is None:
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data.pop("add_friend_name", None)

    await update.message.reply_text(
        text=PROMPT_NAME_MESSAGE,
        reply_markup=get_cancel_keyboard(),
    )
    return WAITING_FOR_NAME


async def process_friend_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Validate the entered friend name and prompt for their birthday.
    """
    if update.message is None:
        return WAITING_FOR_NAME

    raw_text = update.message.text or ""

    if raw_text.strip() in {"❌ Cancel", "Cancel", "/cancel"}:
        return await cancel_add_friend(update, context)

    is_valid, cleaned_name, error_message = validate_friend_name(raw_text)

    if not is_valid:
        await update.message.reply_text(
            text=f"⚠️ {error_message}",
            reply_markup=get_cancel_keyboard(),
        )
        return WAITING_FOR_NAME

    if context.user_data is not None:
        context.user_data["add_friend_name"] = cleaned_name

    prompt_birthday = (
        f"Great! What is {cleaned_name}'s birthday?\n\n"
        "Please enter in one of the following formats:\n"
        "• DD-MM-YYYY (e.g. 25-12-1995) when birth year is known\n"
        "• DD-MM (e.g. 15-03) when birth year is unknown"
    )

    await update.message.reply_text(
        text=prompt_birthday,
        reply_markup=get_cancel_keyboard(),
    )
    return WAITING_FOR_BIRTHDAY


async def process_friend_birthday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Validate the entered birthday, persist the friend to the database,
    and display a confirmation with human-readable birthday.
    """
    if update.message is None:
        return WAITING_FOR_BIRTHDAY

    raw_text = update.message.text or ""

    if raw_text.strip() in {"❌ Cancel", "Cancel", "/cancel"}:
        return await cancel_add_friend(update, context)

    is_valid, birth_year, birth_month, birth_day, error_message = validate_and_parse_birthday(
        raw_text
    )

    if not is_valid:
        await update.message.reply_text(
            text=f"⚠️ {error_message}",
            reply_markup=get_cancel_keyboard(),
        )
        return WAITING_FOR_BIRTHDAY

    name: Optional[str] = None
    if context.user_data is not None:
        name = context.user_data.get("add_friend_name")

    if not name:
        await update.message.reply_text(
            text="⚠️ Session expired or name was not found. Please tap ➕ Add Friend to try again.",
            reply_markup=get_main_menu_keyboard(),
        )
        if context.user_data is not None:
            context.user_data.pop("add_friend_name", None)
        return ConversationHandler.END

    effective_user = update.effective_user
    if not effective_user:
        await update.message.reply_text(
            text="⚠️ Could not identify your Telegram account. Please try again.",
            reply_markup=get_main_menu_keyboard(),
        )
        if context.user_data is not None:
            context.user_data.pop("add_friend_name", None)
        return ConversationHandler.END

    telegram_id = int(effective_user.id)
    engine = None
    if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict):
        engine = context.bot_data.get("db_engine")

    try:
        _, friend_name, display_birthday = await asyncio.to_thread(
            handle_add_friend,
            telegram_id=telegram_id,
            name=name,
            birth_month=birth_month,
            birth_day=birth_day,
            birth_year=birth_year,
            engine=engine,
        )
    except UserNotRegisteredError:
        logger.warning("Unregistered user %d attempted to add a friend", telegram_id)
        if context.user_data is not None:
            context.user_data.pop("add_friend_name", None)
        await update.message.reply_text(
            text=UNREGISTERED_ERROR_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END
    except Exception as error:
        logger.error(
            "Failed to save friend for user %d: %s",
            telegram_id,
            error,
        )
        if context.user_data is not None:
            context.user_data.pop("add_friend_name", None)
        await update.message.reply_text(
            text=SAVE_ERROR_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data.pop("add_friend_name", None)

    success_message = (
        f"🎉 Successfully added {friend_name}!\n"
        f"🎂 Birthday: {display_birthday}\n\n"
        f"💡 Tip: Send /profile {friend_name} to add relationship details, interests, and notes!"
    )

    await update.message.reply_text(
        text=success_message,
        reply_markup=get_main_menu_keyboard(),
    )
    return ConversationHandler.END


async def cancel_add_friend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the Add Friend conversation, clear state, and return to main menu."""
    if context.user_data is not None:
        context.user_data.pop("add_friend_name", None)

    if update.message:
        await update.message.reply_text(
            text=CANCEL_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
    return ConversationHandler.END


async def invalid_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle non-text messages during name input."""
    if update.message:
        await update.message.reply_text(
            text="⚠️ Please send your friend's name as text, or tap ❌ Cancel to exit:",
            reply_markup=get_cancel_keyboard(),
        )
    return WAITING_FOR_NAME


async def invalid_birthday_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle non-text messages during birthday input."""
    if update.message:
        await update.message.reply_text(
            text="⚠️ Please send the birthday as text in DD-MM-YYYY or DD-MM format (e.g. 25-12-1995 or 15-03), or tap ❌ Cancel to exit:",
            reply_markup=get_cancel_keyboard(),
        )
    return WAITING_FOR_BIRTHDAY


def get_add_friend_conversation_handler() -> ConversationHandler:
    """Build and return the ConversationHandler for the Add Friend flow."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^➕ Add Friend$"), start_add_friend),
            CommandHandler("addfriend", start_add_friend),
        ],
        states={
            WAITING_FOR_NAME: [
                MessageHandler(
                    filters.Regex(r"^(❌ Cancel|Cancel|/cancel|🔙 Main Menu|🔙 Back to Main Menu|Main Menu)$"),
                    cancel_add_friend,
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_friend_name),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, invalid_name_input),
            ],
            WAITING_FOR_BIRTHDAY: [
                MessageHandler(
                    filters.Regex(r"^(❌ Cancel|Cancel|/cancel|🔙 Main Menu|🔙 Back to Main Menu|Main Menu)$"),
                    cancel_add_friend,
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_friend_birthday),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, invalid_birthday_input),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_add_friend),
            MessageHandler(
                filters.Regex(r"^(❌ Cancel|Cancel|/cancel|🔙 Main Menu|🔙 Back to Main Menu|Main Menu)$"),
                cancel_add_friend,
            ),
        ],
        allow_reentry=True,
    )


async def view_my_friends(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the list of friends saved by the authenticated Telegram user."""
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
        friends = await asyncio.to_thread(
            handle_get_friends,
            telegram_id=telegram_id,
            engine=engine,
        )
    except UserNotRegisteredError:
        logger.warning("Unregistered user %d attempted to view friends", telegram_id)
        await update.message.reply_text(
            text=UNREGISTERED_ERROR_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
        return
    except Exception as error:
        logger.error("Failed to retrieve friends for user %d: %s", telegram_id, error)
        await update.message.reply_text(
            text=RETRIEVAL_ERROR_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
        return

    if not friends:
        await update.message.reply_text(
            text=EMPTY_FRIENDS_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
    else:
        await update.message.reply_text(
            text=format_friends_list_message(friends),
            reply_markup=get_friend_list_keyboard(),
        )


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Navigate back to the main menu reply keyboard."""
    if update.message:
        await update.message.reply_text(
            text="🏠 Main Menu",
            reply_markup=get_main_menu_keyboard(),
        )


async def execute_search_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> int:
    """Execute search query for the user and reply with results."""
    if update.message is None:
        return ConversationHandler.END

    effective_user = update.effective_user
    if not effective_user:
        await update.message.reply_text(
            text="⚠️ Could not identify your Telegram account. Please try again.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    telegram_id = int(effective_user.id)
    engine = None
    if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict):
        engine = context.bot_data.get("db_engine")

    try:
        results = await asyncio.to_thread(
            handle_search_friends,
            telegram_id=telegram_id,
            query=query,
            engine=engine,
        )
    except UserNotRegisteredError:
        logger.warning("Unregistered user %d attempted to search friends", telegram_id)
        await update.message.reply_text(
            text=UNREGISTERED_ERROR_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END
    except Exception as error:
        logger.error("Failed to search friends for user %d: %s", telegram_id, error)
        await update.message.reply_text(
            text=SEARCH_ERROR_MESSAGE,
            reply_markup=get_friend_list_keyboard(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        text=format_search_results_message(query, results),
        reply_markup=get_friend_list_keyboard(),
    )
    return ConversationHandler.END


async def search_friends_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle /search command."""
    if update.message is None:
        return ConversationHandler.END

    args = getattr(context, "args", None)
    if args:
        query = " ".join(args).strip()
        return await execute_search_and_reply(update, context, query)

    return await start_search_friend(update, context)


async def start_search_friend(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Prompt user for a search query."""
    if update.message is None:
        return ConversationHandler.END

    await update.message.reply_text(
        text=SEARCH_PROMPT_MESSAGE,
        reply_markup=get_cancel_keyboard(),
    )
    return WAITING_FOR_SEARCH_QUERY


async def process_search_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Process search query entered by the user."""
    if update.message is None:
        return WAITING_FOR_SEARCH_QUERY

    raw_text = (update.message.text or "").strip()

    if raw_text in {"❌ Cancel", "Cancel", "/cancel"}:
        return await cancel_search_friend(update, context)

    if raw_text in {"🔙 Main Menu", "🔙 Back to Main Menu", "Main Menu"}:
        return await cancel_search_to_main_menu(update, context)

    if not raw_text:
        await update.message.reply_text(
            text="⚠️ Search query cannot be blank. Please enter a name to search, or tap ❌ Cancel:",
            reply_markup=get_cancel_keyboard(),
        )
        return WAITING_FOR_SEARCH_QUERY

    return await execute_search_and_reply(update, context, raw_text)


async def cancel_search_friend(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Cancel the search flow and return user to the friend list view."""
    if update.message:
        await update.message.reply_text(
            text=SEARCH_CANCEL_MESSAGE,
            reply_markup=get_friend_list_keyboard(),
        )
    return ConversationHandler.END


async def cancel_search_to_main_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Cancel search and navigate back to main menu."""
    if update.message:
        await update.message.reply_text(
            text="🏠 Main Menu",
            reply_markup=get_main_menu_keyboard(),
        )
    return ConversationHandler.END


async def invalid_search_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle non-text messages during search prompt."""
    if update.message:
        await update.message.reply_text(
            text="⚠️ Please send the friend's name as text, or tap ❌ Cancel to exit:",
            reply_markup=get_cancel_keyboard(),
        )
    return WAITING_FOR_SEARCH_QUERY


def get_search_friend_conversation_handler() -> ConversationHandler:
    """Build and return the ConversationHandler for interactive friend search."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^🔍 Search Friend$"), start_search_friend),
            CommandHandler("search", search_friends_command),
        ],
        states={
            WAITING_FOR_SEARCH_QUERY: [
                MessageHandler(
                    filters.Regex(r"^(❌ Cancel|Cancel|/cancel)$"),
                    cancel_search_friend,
                ),
                MessageHandler(
                    filters.Regex(r"^(🔙 Main Menu|🔙 Back to Main Menu|Main Menu)$"),
                    cancel_search_to_main_menu,
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_search_query),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, invalid_search_input),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_search_friend),
            MessageHandler(
                filters.Regex(r"^(❌ Cancel|Cancel|/cancel)$"),
                cancel_search_friend,
            ),
            MessageHandler(
                filters.Regex(r"^(🔙 Main Menu|🔙 Back to Main Menu|Main Menu)$"),
                cancel_search_to_main_menu,
            ),
        ],
        allow_reentry=True,
    )


# ==============================================================================
# Friend Profile & Preference Handlers (Stage 9)
# ==============================================================================


async def open_friend_profile_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /profile and /friend commands.
    Usage:
        /profile           -> lists friends as inline buttons to pick
        /profile <name>    -> opens profile for the named friend
        /profile <id>      -> opens profile by friend primary key ID
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
    if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict):
        engine = context.bot_data.get("db_engine")

    args = getattr(context, "args", None)

    # 1. No arguments: show inline buttons for all friends
    if not args:
        try:
            friends = await asyncio.to_thread(
                handle_get_friends, telegram_id=telegram_id, engine=engine
            )
        except UserNotRegisteredError:
            await update.message.reply_text(
                text=UNREGISTERED_ERROR_MESSAGE, reply_markup=get_main_menu_keyboard()
            )
            return
        except Exception as err:
            logger.error("Failed to get friends for /profile: %s", err)
            await update.message.reply_text(text=RETRIEVAL_ERROR_MESSAGE)
            return

        if not friends:
            await update.message.reply_text(
                text="👥 You haven't added any friends yet!\nTap \"➕ Add Friend\" to add someone first.",
                reply_markup=get_main_menu_keyboard(),
            )
            return

        await update.message.reply_text(
            text="👤 Friend Profiles\n\nTap a friend below to view and manage their profile details:",
            reply_markup=get_friends_inline_keyboard(friends),
        )
        return

    # 2. Argument provided: lookup by ID or name
    query = " ".join(args).strip()

    if query.isdigit():
        target_friend_id = int(query)
        try:
            profile = await asyncio.to_thread(
                handle_get_friend_profile,
                telegram_id=telegram_id,
                friend_id=target_friend_id,
                engine=engine,
            )
            await update.message.reply_text(
                text=format_friend_profile_message(profile),
                reply_markup=get_friend_profile_inline_keyboard(target_friend_id),
            )
            return
        except (FriendNotFoundError, UserNotRegisteredError):
            pass

    # Lookup by name
    try:
        matches = await asyncio.to_thread(
            handle_search_friends,
            telegram_id=telegram_id,
            query=query,
            engine=engine,
        )
    except UserNotRegisteredError:
        await update.message.reply_text(
            text=UNREGISTERED_ERROR_MESSAGE, reply_markup=get_main_menu_keyboard()
        )
        return
    except Exception as err:
        logger.error("Failed to search friend for /profile: %s", err)
        await update.message.reply_text(text=SEARCH_ERROR_MESSAGE)
        return

    if not matches:
        await update.message.reply_text(
            text=f"🔍 No friends found matching \"{query}\". Send /profile to view all friends.",
            reply_markup=get_friend_list_keyboard(),
        )
        return

    if len(matches) == 1:
        friend_id = matches[0]["id"]
        profile = await asyncio.to_thread(
            handle_get_friend_profile,
            telegram_id=telegram_id,
            friend_id=friend_id,
            engine=engine,
        )
        await update.message.reply_text(
            text=format_friend_profile_message(profile),
            reply_markup=get_friend_profile_inline_keyboard(friend_id),
        )
    else:
        await update.message.reply_text(
            text=f"Multiple friends found matching \"{query}\". Tap a friend below:",
            reply_markup=get_friends_inline_keyboard(matches),
        )



async def _safe_edit_callback_message(query: Any, text: str, reply_markup: Any = None) -> None:
    """Edit callback query message supporting query.edit_message_text and query.message.edit_message_text."""
    if hasattr(query, "edit_message_text"):
        res = query.edit_message_text(text=text, reply_markup=reply_markup)
        if hasattr(res, "__await__"):
            await res
    elif getattr(query, "message", None) and hasattr(query.message, "edit_message_text"):
        res = query.message.edit_message_text(text=text, reply_markup=reply_markup)
        if hasattr(res, "__await__"):
            await res


async def friend_profile_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle interactive callbacks for friend profile display, presets, and clearing.
    """
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

    # 1. Back to friends list
    if data == "fprof_back_list":
        await query.answer()
        try:
            friends = await asyncio.to_thread(
                handle_get_friends, telegram_id=telegram_id, engine=engine
            )
        except Exception as err:
            logger.error("Failed to load friends on back: %s", err)
            friends = []

        if not friends:
            await _safe_edit_callback_message(query, text=EMPTY_FRIENDS_MESSAGE)
        else:
            await _safe_edit_callback_message(
                query,
                text="👤 Friend Profiles\n\nTap a friend below to view and manage their profile details:",
                reply_markup=get_friends_inline_keyboard(friends),
            )
        return

    # 2. View friend profile: fprof_view:<friend_id>
    if data.startswith("fprof_view:"):
        friend_id = parse_callback_id(data, "fprof_view:")
        if friend_id is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        try:
            profile = await asyncio.to_thread(
                handle_get_friend_profile,
                telegram_id=telegram_id,
                friend_id=friend_id,
                engine=engine,
            )
            await query.answer()
            await _safe_edit_callback_message(
                query,
                text=format_friend_profile_message(profile),
                reply_markup=get_friend_profile_inline_keyboard(friend_id),
            )
        except FriendNotFoundError:
            await query.answer("Friend not found or permission denied.")
        except Exception as err:
            logger.error("Failed to load friend profile %d: %s", friend_id, err)
            await query.answer("Error loading profile.")
        return

    # 3. Relationship preset menu: fprof_rel_menu:<friend_id>
    if data.startswith("fprof_rel_menu:"):
        friend_id = parse_callback_id(data, "fprof_rel_menu:")
        if friend_id is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        try:
            profile = await asyncio.to_thread(
                handle_get_friend_profile,
                telegram_id=telegram_id,
                friend_id=friend_id,
                engine=engine,
            )
            await query.answer()
            await _safe_edit_callback_message(
                query,
                text=f"👥 Select relationship for {profile['name']}:\n\nChoose a preset below or tap '✍️ Custom / Type':",
                reply_markup=get_relationship_presets_inline_keyboard(friend_id),
            )
        except Exception as err:
            logger.error("Failed to show relationship menu %d: %s", friend_id, err)
            await query.answer("Error opening relationship menu.")
        return

    # 4. Set relationship preset: fprof_set_rel:<friend_id>:<value>
    if data.startswith("fprof_set_rel:"):
        parsed = parse_callback_id_and_value(data, "fprof_set_rel:")
        if parsed is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        friend_id, rel_value = parsed
        try:
            updated = await asyncio.to_thread(
                handle_update_friend_profile,
                telegram_id=telegram_id,
                friend_id=friend_id,
                relationship=rel_value,
                engine=engine,
            )
            await query.answer(f"Relationship set to {rel_value}!")
            await _safe_edit_callback_message(
                query,
                text=format_friend_profile_message(updated),
                reply_markup=get_friend_profile_inline_keyboard(friend_id),
            )
        except Exception as err:
            logger.error("Failed to update relationship preset %d: %s", friend_id, err)
            await query.answer("Failed to update relationship.")
        return

    # 5. Wish style preset menu: fprof_style_menu:<friend_id>
    if data.startswith("fprof_style_menu:"):
        friend_id = parse_callback_id(data, "fprof_style_menu:")
        if friend_id is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        try:
            profile = await asyncio.to_thread(
                handle_get_friend_profile,
                telegram_id=telegram_id,
                friend_id=friend_id,
                engine=engine,
            )
            await query.answer()
            await _safe_edit_callback_message(
                query,
                text=f"✨ Select preferred birthday-wish style for {profile['name']}:\n\nChoose a preset below or tap '✍️ Custom / Type':",
                reply_markup=get_wish_style_presets_inline_keyboard(friend_id),
            )
        except Exception as err:
            logger.error("Failed to show wish style menu %d: %s", friend_id, err)
            await query.answer("Error opening style menu.")
        return

    # 6. Set wish style preset: fprof_set_style:<friend_id>:<value>
    if data.startswith("fprof_set_style:"):
        parsed = parse_callback_id_and_value(data, "fprof_set_style:")
        if parsed is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        friend_id, style_value = parsed
        try:
            updated = await asyncio.to_thread(
                handle_update_friend_profile,
                telegram_id=telegram_id,
                friend_id=friend_id,
                wish_style=style_value,
                engine=engine,
            )
            await query.answer(f"Wish style set to {style_value}!")
            await _safe_edit_callback_message(
                query,
                text=format_friend_profile_message(updated),
                reply_markup=get_friend_profile_inline_keyboard(friend_id),
            )
        except Exception as err:
            logger.error("Failed to update wish style preset %d: %s", friend_id, err)
            await query.answer("Failed to update wish style.")
        return

    # 7. Clear profile: fprof_clear:<friend_id>
    if data.startswith("fprof_clear:"):
        friend_id = parse_callback_id(data, "fprof_clear:")
        if friend_id is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        try:
            cleared = await asyncio.to_thread(
                handle_clear_friend_profile,
                telegram_id=telegram_id,
                friend_id=friend_id,
                engine=engine,
            )
            await query.answer("Profile details cleared.")
            await _safe_edit_callback_message(
                query,
                text=format_friend_profile_message(cleared),
                reply_markup=get_friend_profile_inline_keyboard(friend_id),
            )
        except Exception as err:
            logger.error("Failed to clear friend profile %d: %s", friend_id, err)
            await query.answer("Failed to clear profile.")
        return

    # Fallback for unrecognized or malformed friend profile callbacks
    await query.answer("⚠️ Unrecognized option.")


# ==============================================================================
# Profile Custom Text Conversation Handlers
# ==============================================================================


async def _verify_friend_profile_ownership(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    friend_id: int,
) -> Optional[Dict[str, Any]]:
    """
    Verify that the friend exists and belongs to the authenticated Telegram user.
    Rejects unauthorized access without displaying editing prompts or modifying data.
    """
    query = update.callback_query
    effective_user = update.effective_user
    if not effective_user:
        if query and hasattr(query, "answer"):
            try:
                res = query.answer("Could not identify user.")
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                pass
        return None

    try:
        telegram_id = int(effective_user.id)
    except (TypeError, ValueError):
        if query and hasattr(query, "answer"):
            try:
                res = query.answer("Invalid user identification.")
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                pass
        return None

    engine = None
    if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict):
        engine = context.bot_data.get("db_engine")

    try:
        return await asyncio.to_thread(
            handle_get_friend_profile,
            telegram_id=telegram_id,
            friend_id=friend_id,
            engine=engine,
        )
    except (FriendNotFoundError, UserNotRegisteredError):
        if query and hasattr(query, "answer"):
            try:
                res = query.answer("Friend not found or permission denied.")
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                pass
        return None
    except Exception as err:
        logger.error("Failed to verify friend %d ownership: %s", friend_id, err)
        if query and hasattr(query, "answer"):
            try:
                res = query.answer("Error accessing friend profile.")
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                pass
        return None


async def start_prompt_relationship(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Prompt user for custom relationship text."""
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END

    friend_id = parse_callback_id(query.data, "fprof_prompt_rel:")
    if friend_id is None:
        await query.answer("⚠️ Invalid or expired request.", show_alert=True)
        return ConversationHandler.END
    profile = await _verify_friend_profile_ownership(update, context, friend_id)
    if profile is None:
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data["editing_friend_id"] = friend_id
        context.user_data["editing_state"] = WAITING_FOR_PROFILE_RELATIONSHIP

    await query.answer()
    if query.message:
        await query.message.reply_text(
            text=(
                "👥 Custom Relationship\n\n"
                "Please type your relationship with this friend (e.g. Mentor, Gym Buddy, Childhood Friend):\n\n"
                "Send /cancel or tap ❌ Cancel to abort."
            ),
            reply_markup=get_cancel_keyboard(),
        )
    return WAITING_FOR_PROFILE_RELATIONSHIP


async def process_profile_relationship(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Validate and persist custom relationship."""
    if update.message is None:
        return WAITING_FOR_PROFILE_RELATIONSHIP

    raw_text = (update.message.text or "").strip()
    if raw_text in {"❌ Cancel", "Cancel", "/cancel"}:
        return await cancel_profile_input(update, context)

    is_valid, cleaned, error_msg = validate_relationship(raw_text)
    if not is_valid:
        await update.message.reply_text(
            text=f"⚠️ {error_msg}", reply_markup=get_cancel_keyboard()
        )
        return WAITING_FOR_PROFILE_RELATIONSHIP

    friend_id = context.user_data.get("editing_friend_id") if context.user_data else None
    if not friend_id:
        await update.message.reply_text(
            text="⚠️ Session expired. Please view profile and try again.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    effective_user = update.effective_user
    telegram_id = int(effective_user.id) if effective_user else 0
    engine = context.bot_data.get("db_engine") if hasattr(context, "bot_data") and isinstance(context.bot_data, dict) else None

    try:
        updated = await asyncio.to_thread(
            handle_update_friend_profile,
            telegram_id=telegram_id,
            friend_id=friend_id,
            relationship=cleaned,
            engine=engine,
        )
    except Exception as err:
        logger.error("Failed to update relationship for friend %d: %s", friend_id, err)
        await update.message.reply_text(
            text="⚠️ An unexpected error occurred while saving relationship.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data.pop("editing_friend_id", None)
        context.user_data.pop("editing_state", None)

    await update.message.reply_text(
        text=f"✅ Relationship successfully set to: {cleaned}",
        reply_markup=get_main_menu_keyboard(),
    )
    await update.message.reply_text(
        text=format_friend_profile_message(updated),
        reply_markup=get_friend_profile_inline_keyboard(friend_id),
    )
    return ConversationHandler.END


async def start_prompt_interests(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Prompt user for interests/hobbies text."""
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END

    friend_id = parse_callback_id(query.data, "fprof_prompt_interests:")
    if friend_id is None:
        await query.answer("⚠️ Invalid or expired request.", show_alert=True)
        return ConversationHandler.END
    profile = await _verify_friend_profile_ownership(update, context, friend_id)
    if profile is None:
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data["editing_friend_id"] = friend_id
        context.user_data["editing_state"] = WAITING_FOR_PROFILE_INTERESTS

    await query.answer()
    if query.message:
        await query.message.reply_text(
            text=(
                "🎨 Update Interests & Hobbies\n\n"
                "Please enter your friend's interests or hobbies (e.g. Photography, Hiking, Sci-Fi movies):\n\n"
                "Send /cancel or tap ❌ Cancel to abort."
            ),
            reply_markup=get_cancel_keyboard(),
        )
    return WAITING_FOR_PROFILE_INTERESTS


async def process_profile_interests(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Validate and persist interests/hobbies text."""
    if update.message is None:
        return WAITING_FOR_PROFILE_INTERESTS

    raw_text = (update.message.text or "").strip()
    if raw_text in {"❌ Cancel", "Cancel", "/cancel"}:
        return await cancel_profile_input(update, context)

    is_valid, cleaned, error_msg = validate_interests(raw_text)
    if not is_valid:
        await update.message.reply_text(
            text=f"⚠️ {error_msg}", reply_markup=get_cancel_keyboard()
        )
        return WAITING_FOR_PROFILE_INTERESTS

    friend_id = context.user_data.get("editing_friend_id") if context.user_data else None
    if not friend_id:
        await update.message.reply_text(
            text="⚠️ Session expired. Please view profile and try again.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    effective_user = update.effective_user
    telegram_id = int(effective_user.id) if effective_user else 0
    engine = context.bot_data.get("db_engine") if hasattr(context, "bot_data") and isinstance(context.bot_data, dict) else None

    try:
        updated = await asyncio.to_thread(
            handle_update_friend_profile,
            telegram_id=telegram_id,
            friend_id=friend_id,
            interests=cleaned,
            engine=engine,
        )
    except Exception as err:
        logger.error("Failed to update interests for friend %d: %s", friend_id, err)
        await update.message.reply_text(
            text="⚠️ An unexpected error occurred while saving interests.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data.pop("editing_friend_id", None)
        context.user_data.pop("editing_state", None)

    await update.message.reply_text(
        text=f"✅ Interests successfully updated!",
        reply_markup=get_main_menu_keyboard(),
    )
    await update.message.reply_text(
        text=format_friend_profile_message(updated),
        reply_markup=get_friend_profile_inline_keyboard(friend_id),
    )
    return ConversationHandler.END


async def start_prompt_notes(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Prompt user for personal notes / meaningful memories."""
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END

    friend_id = parse_callback_id(query.data, "fprof_prompt_notes:")
    if friend_id is None:
        await query.answer("⚠️ Invalid or expired request.", show_alert=True)
        return ConversationHandler.END
    profile = await _verify_friend_profile_ownership(update, context, friend_id)
    if profile is None:
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data["editing_friend_id"] = friend_id
        context.user_data["editing_state"] = WAITING_FOR_PROFILE_NOTES

    await query.answer()
    if query.message:
        await query.message.reply_text(
            text=(
                "📝 Update Personal Notes & Memories\n\n"
                "Please enter personal notes, meaningful memories, or gift ideas for your friend:\n\n"
                "Send /cancel or tap ❌ Cancel to abort."
            ),
            reply_markup=get_cancel_keyboard(),
        )
    return WAITING_FOR_PROFILE_NOTES


async def process_profile_notes(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Validate and persist personal notes."""
    if update.message is None:
        return WAITING_FOR_PROFILE_NOTES

    raw_text = (update.message.text or "").strip()
    if raw_text in {"❌ Cancel", "Cancel", "/cancel"}:
        return await cancel_profile_input(update, context)

    is_valid, cleaned, error_msg = validate_notes(raw_text)
    if not is_valid:
        await update.message.reply_text(
            text=f"⚠️ {error_msg}", reply_markup=get_cancel_keyboard()
        )
        return WAITING_FOR_PROFILE_NOTES

    friend_id = context.user_data.get("editing_friend_id") if context.user_data else None
    if not friend_id:
        await update.message.reply_text(
            text="⚠️ Session expired. Please view profile and try again.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    effective_user = update.effective_user
    telegram_id = int(effective_user.id) if effective_user else 0
    engine = context.bot_data.get("db_engine") if hasattr(context, "bot_data") and isinstance(context.bot_data, dict) else None

    try:
        updated = await asyncio.to_thread(
            handle_update_friend_profile,
            telegram_id=telegram_id,
            friend_id=friend_id,
            notes=cleaned,
            engine=engine,
        )
    except Exception as err:
        logger.error("Failed to update notes for friend %d: %s", friend_id, err)
        await update.message.reply_text(
            text="⚠️ An unexpected error occurred while saving personal notes.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data.pop("editing_friend_id", None)
        context.user_data.pop("editing_state", None)

    await update.message.reply_text(
        text=f"✅ Personal notes successfully updated!",
        reply_markup=get_main_menu_keyboard(),
    )
    await update.message.reply_text(
        text=format_friend_profile_message(updated),
        reply_markup=get_friend_profile_inline_keyboard(friend_id),
    )
    return ConversationHandler.END


async def start_prompt_wish_style(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Prompt user for custom wish style."""
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END

    friend_id = parse_callback_id(query.data, "fprof_prompt_style:")
    if friend_id is None:
        await query.answer("⚠️ Invalid or expired request.", show_alert=True)
        return ConversationHandler.END
    profile = await _verify_friend_profile_ownership(update, context, friend_id)
    if profile is None:
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data["editing_friend_id"] = friend_id
        context.user_data["editing_state"] = WAITING_FOR_PROFILE_WISH_STYLE

    await query.answer()
    if query.message:
        await query.message.reply_text(
            text=(
                "✨ Custom Wish Style\n\n"
                "Please type the preferred birthday-wish style (e.g. Sarcastic, Inspiring, Rhyming poem):\n\n"
                "Send /cancel or tap ❌ Cancel to abort."
            ),
            reply_markup=get_cancel_keyboard(),
        )
    return WAITING_FOR_PROFILE_WISH_STYLE


async def process_profile_wish_style(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Validate and persist custom wish style."""
    if update.message is None:
        return WAITING_FOR_PROFILE_WISH_STYLE

    raw_text = (update.message.text or "").strip()
    if raw_text in {"❌ Cancel", "Cancel", "/cancel"}:
        return await cancel_profile_input(update, context)

    is_valid, cleaned, error_msg = validate_wish_style(raw_text)
    if not is_valid:
        await update.message.reply_text(
            text=f"⚠️ {error_msg}", reply_markup=get_cancel_keyboard()
        )
        return WAITING_FOR_PROFILE_WISH_STYLE

    friend_id = context.user_data.get("editing_friend_id") if context.user_data else None
    if not friend_id:
        await update.message.reply_text(
            text="⚠️ Session expired. Please view profile and try again.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    effective_user = update.effective_user
    telegram_id = int(effective_user.id) if effective_user else 0
    engine = context.bot_data.get("db_engine") if hasattr(context, "bot_data") and isinstance(context.bot_data, dict) else None

    try:
        updated = await asyncio.to_thread(
            handle_update_friend_profile,
            telegram_id=telegram_id,
            friend_id=friend_id,
            wish_style=cleaned,
            engine=engine,
        )
    except Exception as err:
        logger.error("Failed to update wish style for friend %d: %s", friend_id, err)
        await update.message.reply_text(
            text="⚠️ An unexpected error occurred while saving wish style.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data.pop("editing_friend_id", None)
        context.user_data.pop("editing_state", None)

    await update.message.reply_text(
        text=f"✅ Preferred wish style set to: {cleaned}",
        reply_markup=get_main_menu_keyboard(),
    )
    await update.message.reply_text(
        text=format_friend_profile_message(updated),
        reply_markup=get_friend_profile_inline_keyboard(friend_id),
    )
    return ConversationHandler.END


async def cancel_profile_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Cancel profile input and restore main menu."""
    if context.user_data is not None:
        context.user_data.pop("editing_friend_id", None)
        context.user_data.pop("editing_state", None)

    if update.callback_query and hasattr(update.callback_query, "answer"):
        try:
            res = update.callback_query.answer()
            if hasattr(res, "__await__"):
                await res
        except Exception:
            pass

    target_msg = update.message or (update.callback_query.message if update.callback_query else None)
    if target_msg and hasattr(target_msg, "reply_text"):
        await target_msg.reply_text(
            text=CANCEL_PROFILE_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
    return ConversationHandler.END


async def invalid_profile_text_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Optional[int]:
    """Handle non-text messages during profile text input."""
    if update.message:
        await update.message.reply_text(
            text="⚠️ Text is required. Please send your input as text, or tap ❌ Cancel to exit:",
            reply_markup=get_cancel_keyboard(),
        )
    if context.user_data is not None:
        return context.user_data.get("editing_state")
    return None


def get_friend_profile_conversation_handler() -> ConversationHandler:
    """
    Build and return ConversationHandler for custom profile field text inputs.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=PTBUserWarning)
        return ConversationHandler(
            entry_points=[
                CallbackQueryHandler(start_prompt_relationship, pattern=r"^fprof_prompt_rel:\d+$"),
                CallbackQueryHandler(start_prompt_interests, pattern=r"^fprof_prompt_interests:\d+$"),
                CallbackQueryHandler(start_prompt_notes, pattern=r"^fprof_prompt_notes:\d+$"),
                CallbackQueryHandler(start_prompt_wish_style, pattern=r"^fprof_prompt_style:\d+$"),
            ],
            states={
                WAITING_FOR_PROFILE_RELATIONSHIP: [
                    MessageHandler(filters.Regex(r"^(❌ Cancel|Cancel|/cancel)$"), cancel_profile_input),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, process_profile_relationship),
                    MessageHandler(~filters.TEXT & ~filters.COMMAND, invalid_profile_text_input),
                ],
                WAITING_FOR_PROFILE_INTERESTS: [
                    MessageHandler(filters.Regex(r"^(❌ Cancel|Cancel|/cancel)$"), cancel_profile_input),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, process_profile_interests),
                    MessageHandler(~filters.TEXT & ~filters.COMMAND, invalid_profile_text_input),
                ],
                WAITING_FOR_PROFILE_NOTES: [
                    MessageHandler(filters.Regex(r"^(❌ Cancel|Cancel|/cancel)$"), cancel_profile_input),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, process_profile_notes),
                    MessageHandler(~filters.TEXT & ~filters.COMMAND, invalid_profile_text_input),
                ],
                WAITING_FOR_PROFILE_WISH_STYLE: [
                    MessageHandler(filters.Regex(r"^(❌ Cancel|Cancel|/cancel)$"), cancel_profile_input),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, process_profile_wish_style),
                    MessageHandler(~filters.TEXT & ~filters.COMMAND, invalid_profile_text_input),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", cancel_profile_input),
                MessageHandler(filters.Regex(r"^(❌ Cancel|Cancel|/cancel)$"), cancel_profile_input),
                MessageHandler(
                    filters.Regex(
                        r"^(🔙 Main Menu|🔙 Back to Main Menu|Main Menu|➕ Add Friend|👥 My Friends|🎂 Upcoming Birthdays|⚙️ Settings)$"
                    ),
                    cancel_profile_input,
                ),
            ],
            allow_reentry=True,
        )
