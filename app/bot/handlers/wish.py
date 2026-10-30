"""
Handlers for the Stage 10 AI Birthday Wish Generation flow.
"""
import asyncio
import logging
import random
from typing import Any, Optional

from telegram import Update
from telegram.ext import ContextTypes

from app.ai.client import (
    AIError,
    AIQuotaExhaustedError,
    AIResponseError,
    AIRateLimitError,
    AIServiceUnavailableError,
    AITimeoutError,
    MissingApiKeyError,
)
from app.bot.keyboards.main_menu import get_main_menu_keyboard
from app.bot.keyboards.wish_menu import (
    get_wish_error_keyboard,
    get_wish_friend_selection_keyboard,
    get_wish_result_keyboard,
    get_wish_styles_keyboard,
)
from app.services.friend_ser import (
    FriendNotFoundError,
    UserNotRegisteredError,
    handle_get_friend_profile,
    handle_get_friends,
)
from app.services.wish_ser import generate_wish_for_friend

logger = logging.getLogger(__name__)

EMPTY_FRIENDS_WISH_MESSAGE = (
    "✨ Create Birthday Wish\n\n"
    "You haven't added any friends yet!\n"
    "Please add a friend first using \"➕ Add Friend\" before generating birthday wishes."
)

UNREGISTERED_WISH_MESSAGE = (
    "⚠️ You are not registered yet. Please send /start to register before using this feature."
)

MISSING_API_KEY_MESSAGE = (
    "⚠️ AI Wish Generation Unavailable\n\n"
    "The AI service has not been configured yet (missing AI_API_KEY).\n"
    "Please configure the API key in your environment or contact the bot administrator."
)

RATE_LIMIT_MESSAGE = (
    "⚠️ Service Busy (Rate Limit)\n\n"
    "The AI service is experiencing high demand. Please wait a moment and tap \"🔄 Try Again\"."
)

HIGH_DEMAND_MESSAGE = (
    "⚠️ Model High Demand (Temporary)\n\n"
    "The AI model is currently experiencing high demand.\n"
    "Please wait a moment and tap \"🔄 Try Again\"."
)

QUOTA_EXHAUSTED_MESSAGE = (
    "⚠️ Daily AI Limit Reached\n\n"
    "The AI service has reached its usage limit for today.\n"
    "Please try again later or check your API quota."
)

TIMEOUT_MESSAGE = (
    "⏱️ Request Timed Out\n\n"
    "The AI service took too long to generate a wish. Please tap \"🔄 Try Again\"."
)

GENERIC_AI_ERROR_MESSAGE = (
    "⚠️ Generation Failed\n\n"
    "An error occurred while generating the birthday wish. Please try again later."
)

ACCESS_DENIED_MESSAGE = (
    "⚠️ Friend not found or access denied.\n"
    "You can only generate wishes for your own saved friends."
)


def format_wish_display_message(name: str, wish_text: str, style: str) -> str:
    """Format the generated wish into a clean Telegram message."""
    return (
        f"🎂 Birthday Wish for {name}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{wish_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✨ Style: {style}"
    )


async def _safe_edit_callback_message(
    query: Any, text: str, reply_markup: Any = None
) -> None:
    """Safely edit a callback query message supporting both direct and nested calls."""
    if hasattr(query, "edit_message_text"):
        res = query.edit_message_text(text=text, reply_markup=reply_markup)
        if hasattr(res, "__await__"):
            await res
    elif getattr(query, "message", None) and hasattr(query.message, "edit_message_text"):
        res = query.message.edit_message_text(text=text, reply_markup=reply_markup)
        if hasattr(res, "__await__"):
            await res


async def create_wish_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle '✨ Create Wish' button or /wish command.
    Lists the user's friends for wish selection.
    """
    if update.message is None or not update.effective_user:
        return

    telegram_id = int(update.effective_user.id)
    engine = (
        context.bot_data.get("db_engine")
        if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict)
        else None
    )

    try:
        friends = await asyncio.to_thread(
            handle_get_friends, telegram_id=telegram_id, engine=engine
        )
    except UserNotRegisteredError:
        await update.message.reply_text(
            text=UNREGISTERED_WISH_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
        return
    except Exception as err:
        logger.error("Failed to fetch friends for user %d: %s", telegram_id, err)
        await update.message.reply_text(
            text="⚠️ An unexpected error occurred. Please try again later.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    if not friends:
        await update.message.reply_text(
            text=EMPTY_FRIENDS_WISH_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
        return

    await update.message.reply_text(
        text="✨ Create Birthday Wish\n\nSelect a friend to generate a personalized birthday wish:",
        reply_markup=get_wish_friend_selection_keyboard(friends),
    )


async def wish_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle inline callback interactions for the wish generation flow.
    Callbacks:
        - wish_cancel: Return to main menu
        - wish_select:<friend_id>: Generate wish using profile/default style
        - wish_regen:<friend_id>:<style>: Regenerate wish with new variation
        - wish_styles:<friend_id>: Show style picker
        - wish_setstyle:<friend_id>:<style>: Generate wish in explicit style
    """
    query = update.callback_query
    if query is None or query.data is None:
        return

    effective_user = update.effective_user
    if not effective_user:
        await query.answer("Could not identify user.")
        return

    telegram_id = int(effective_user.id)
    engine = (
        context.bot_data.get("db_engine")
        if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict)
        else None
    )
    ai_client = (
        context.bot_data.get("ai_client")
        if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict)
        else None
    )

    data = query.data

    # 1. Cancel / Return to Main Menu
    if data == "wish_cancel":
        await query.answer()
        await _safe_edit_callback_message(query, text="Returning to main menu...")
        if query.message:
            await query.message.reply_text(
                text="🎂 Main Menu",
                reply_markup=get_main_menu_keyboard(),
            )
        return

    # 2. Show style options: wish_styles:<friend_id>
    if data.startswith("wish_styles:"):
        friend_id = int(data.split(":", 1)[1])
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
                text=f"🎨 Select a wish style for {profile['name']}:",
                reply_markup=get_wish_styles_keyboard(friend_id),
            )
        except FriendNotFoundError:
            await query.answer("Friend not found or permission denied.")
            await _safe_edit_callback_message(
                query,
                text=ACCESS_DENIED_MESSAGE,
                reply_markup=get_wish_error_keyboard(),
            )
        except Exception as err:
            logger.error("Failed to show wish styles %d: %s", friend_id, err)
            await query.answer("Error opening styles.")
        return

    # 3. Wish Generation (select, regen, or setstyle)
    friend_id: Optional[int] = None
    style: Optional[str] = None
    seed: Optional[int] = None

    if data.startswith("wish_select:"):
        friend_id = int(data.split(":", 1)[1])
        style = None
    elif data.startswith("wish_regen:"):
        parts = data.split(":", 2)
        friend_id = int(parts[1])
        style = parts[2] if len(parts) > 2 and parts[2] != "None" else None
        seed = random.randint(1000, 99999)
    elif data.startswith("wish_setstyle:"):
        parts = data.split(":", 2)
        friend_id = int(parts[1])
        style = parts[2] if len(parts) > 2 else None

    if friend_id is None:
        return

    # Check friend existence & ownership first
    try:
        profile = await asyncio.to_thread(
            handle_get_friend_profile,
            telegram_id=telegram_id,
            friend_id=friend_id,
            engine=engine,
        )
    except (FriendNotFoundError, UserNotRegisteredError):
        await query.answer("Access denied.")
        await _safe_edit_callback_message(
            query,
            text=ACCESS_DENIED_MESSAGE,
            reply_markup=get_wish_error_keyboard(),
        )
        return

    await query.answer()
    loading_text = f"⏳ Generating a personalized birthday wish for {profile['name']}..."
    await _safe_edit_callback_message(query, text=loading_text)

    try:
        result = await generate_wish_for_friend(
            telegram_id=telegram_id,
            friend_id=friend_id,
            style=style,
            variation_seed=seed,
            engine=engine,
            ai_client=ai_client,
        )

        display_text = format_wish_display_message(
            name=profile["name"],
            wish_text=result["wish"],
            style=result["style"],
        )
        await _safe_edit_callback_message(
            query,
            text=display_text,
            reply_markup=get_wish_result_keyboard(friend_id, result["style"]),
        )

    except MissingApiKeyError:
        logger.warning("AI Wish generation requested with missing API key")
        await _safe_edit_callback_message(
            query,
            text=MISSING_API_KEY_MESSAGE,
            reply_markup=get_wish_error_keyboard(),
        )
    except AIServiceUnavailableError:
        logger.warning("AI Wish generation temporary high demand (HTTP 503) for friend %d", friend_id)
        await _safe_edit_callback_message(
            query,
            text=HIGH_DEMAND_MESSAGE,
            reply_markup=get_wish_error_keyboard(friend_id),
        )
    except AIQuotaExhaustedError:
        logger.warning("AI Wish generation quota exhausted for friend %d", friend_id)
        await _safe_edit_callback_message(
            query,
            text=QUOTA_EXHAUSTED_MESSAGE,
            reply_markup=get_wish_error_keyboard(friend_id),
        )
    except AIRateLimitError:
        await _safe_edit_callback_message(
            query,
            text=RATE_LIMIT_MESSAGE,
            reply_markup=get_wish_error_keyboard(friend_id),
        )
    except AITimeoutError:
        await _safe_edit_callback_message(
            query,
            text=TIMEOUT_MESSAGE,
            reply_markup=get_wish_error_keyboard(friend_id),
        )
    except (AIResponseError, AIError) as err:
        logger.error("AI error generating wish for friend %d: %s", friend_id, err)
        await _safe_edit_callback_message(
            query,
            text=GENERIC_AI_ERROR_MESSAGE,
            reply_markup=get_wish_error_keyboard(friend_id),
        )
    except (FriendNotFoundError, UserNotRegisteredError):
        await _safe_edit_callback_message(
            query,
            text=ACCESS_DENIED_MESSAGE,
            reply_markup=get_wish_error_keyboard(),
        )
    except Exception as err:
        logger.error("Unexpected error in wish generation: %s", err)
        await _safe_edit_callback_message(
            query,
            text=GENERIC_AI_ERROR_MESSAGE,
            reply_markup=get_wish_error_keyboard(friend_id),
        )
