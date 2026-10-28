"""
Handlers for the Add Friend conversation flow.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional
from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot.keyboards.friend_menu import (
    get_cancel_keyboard,
    get_friend_list_keyboard,
)
from app.bot.keyboards.main_menu import get_main_menu_keyboard
from app.bot.states.friend_states import (
    WAITING_FOR_BIRTHDAY,
    WAITING_FOR_NAME,
    WAITING_FOR_SEARCH_QUERY,
)
from app.services.friend_ser import (
    UserNotRegisteredError,
    handle_add_friend,
    handle_get_friends,
    handle_search_friends,
)
from app.utils.validators import (
    validate_and_parse_birthday,
    validate_friend_name,
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


def format_friends_list_message(friends: List[Dict[str, Any]]) -> str:
    """Format list of friends into a clean, readable message."""
    if not friends:
        return EMPTY_FRIENDS_MESSAGE
    lines = [f"👥 My Friends ({len(friends)})\n"]
    for item in friends:
        lines.append(f"• {item['name']} — {item['display_birthday']}")
    lines.append("\n💡 Tip: Tap \"🔍 Search Friend\" or send /search <name> to find someone.")
    return "\n".join(lines)


def format_search_results_message(query: str, results: List[Dict[str, Any]]) -> str:
    """Format search query results into a readable message."""
    if not results:
        return f"🔍 No friends found matching \"{query}\"."
    lines = [f"🔍 Search results for \"{query}\" ({len(results)}):\n"]
    for item in results:
        lines.append(f"• {item['name']} — {item['display_birthday']}")
    return "\n".join(lines)


async def start_add_friend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Start the guided Add Friend conversation flow.
    Invoked when a user taps '➕ Add Friend' or sends /addfriend.
    """
    if update.message is None:
        return ConversationHandler.END

    # Ensure previous temporary conversation state is cleared
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

    # Check for cancel keywords
    if raw_text.strip() in {"❌ Cancel", "Cancel", "/cancel"}:
        return await cancel_add_friend(update, context)

    is_valid, cleaned_name, error_message = validate_friend_name(raw_text)

    if not is_valid:
        await update.message.reply_text(
            text=f"⚠️ {error_message}",
            reply_markup=get_cancel_keyboard(),
        )
        return WAITING_FOR_NAME

    # Save cleaned name in temporary conversation state
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

    # Check for cancel keywords
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
        # Fallback in case conversation state was lost
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

    # Retrieve engine from context if available (e.g. in test fixtures or custom setups)
    engine = None
    if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict):
        engine = context.bot_data.get("db_engine")

    try:
        # Offload synchronous database persistence to a worker thread to keep the event loop unblocked
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

    # Clean up temporary conversation state after successful save
    if context.user_data is not None:
        context.user_data.pop("add_friend_name", None)

    success_message = (
        f"🎉 Successfully added {friend_name}!\n"
        f"🎂 Birthday: {display_birthday}"
    )

    await update.message.reply_text(
        text=success_message,
        reply_markup=get_main_menu_keyboard(),
    )
    return ConversationHandler.END


async def cancel_add_friend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Cancel the Add Friend conversation, clear temporary state,
    and return user to the main menu.
    """
    if context.user_data is not None:
        context.user_data.pop("add_friend_name", None)

    if update.message:
        await update.message.reply_text(
            text=CANCEL_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
    return ConversationHandler.END


async def invalid_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle non-text messages (e.g. photos, stickers) during name input."""
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
    """
    Build and return the ConversationHandler for the Add Friend flow.
    """
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
    """
    Display the list of friends saved by the authenticated Telegram user.
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
    """
    Navigate back to the main menu reply keyboard.
    """
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
    """
    Execute a search against the database for the authenticated user and reply with results.
    """
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
    """
    Handle /search command.
    If query arguments are provided (e.g. /search Alice), performs direct search.
    If no arguments are provided, prompts the user and enters WAITING_FOR_SEARCH_QUERY.
    """
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
    """
    Start the interactive search flow by prompting the user for a friend's name.
    """
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
    """
    Process search query entered by the user.
    """
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
    """
    Cancel the search flow and return user to the friend list view.
    """
    if update.message:
        await update.message.reply_text(
            text=SEARCH_CANCEL_MESSAGE,
            reply_markup=get_friend_list_keyboard(),
        )
    return ConversationHandler.END


async def cancel_search_to_main_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Cancel search and navigate back to main menu.
    """
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
    """
    Build and return the ConversationHandler for interactive friend search.
    """
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
