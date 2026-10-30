"""
Handlers for AI Birthday Wish Generation, Manual Editing, Approval,
Sending, Copying, and History Management.
"""
import asyncio
import logging
import random
import warnings
from datetime import datetime
from typing import Any, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.warnings import PTBUserWarning

warnings.filterwarnings("ignore", message=r".*per_message.*", category=PTBUserWarning)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

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
    get_copy_view_keyboard,
    get_wish_approved_keyboard,
    get_wish_cancel_edit_keyboard,
    get_wish_draft_keyboard,
    get_wish_error_keyboard,
    get_wish_friend_selection_keyboard,
    get_wish_history_keyboard,
    get_wish_result_keyboard,
    get_wish_sent_keyboard,
    get_wish_styles_keyboard,
)
from app.database.models.wish import WishStatus
from app.services.friend_ser import (
    FriendNotFoundError,
    UserNotRegisteredError,
    handle_get_friend_profile,
    handle_get_friends,
)
from app.services.wish_ser import (
    EmptyWishContentError,
    WishNotApprovedError,
    WishNotFoundError,
    create_or_regenerate_wish,
    generate_wish_for_friend,
    handle_approve_wish,
    handle_edit_wish,
    handle_get_user_wishes,
    handle_get_wish,
    handle_link_friend_telegram,
    handle_send_wish,
)
from app.utils.callback_utils import (
    parse_callback_id,
    parse_callback_id_and_value,
    parse_callback_page,
)

logger = logging.getLogger(__name__)

# State for Wish Editing ConversationHandler
WAITING_FOR_WISH_EDIT_TEXT = 1

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
    "You can only manage wishes for your own saved friends."
)


def format_wish_display_message(
    name: str,
    wish_text: str,
    style: str,
    status: str = "DRAFT",
    created_at: Optional[datetime] = None,
) -> str:
    """Format the wish into a clean, structured Telegram message."""
    status_str = "📝 Draft (Pending Approval)"
    if status == WishStatus.APPROVED:
        status_str = "✅ Approved"
    elif status == WishStatus.SENT:
        status_str = "🚀 Sent"

    lines = [
        f"🎂 Birthday Wish for {name}",
        "━━━━━━━━━━━━━━━━━━━━",
        wish_text,
        "━━━━━━━━━━━━━━━━━━━━",
        f"✨ Style: {style}",
        f"📌 Status: {status_str}",
    ]
    if created_at:
        lines.append(f"📅 Created: {created_at.strftime('%Y-%m-%d %H:%M UTC')}")

    return "\n".join(lines)


async def _safe_edit_callback_message(
    query: Any, text: str, reply_markup: Any = None, parse_mode: Optional[str] = None
) -> None:
    """Safely edit a callback query message supporting both direct and nested calls."""
    if hasattr(query, "edit_message_text"):
        res = query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        if hasattr(res, "__await__"):
            await res
    elif getattr(query, "message", None) and hasattr(query.message, "edit_message_text"):
        res = query.message.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        if hasattr(res, "__await__"):
            await res


def _get_engine_and_client(context: ContextTypes.DEFAULT_TYPE):
    """Extract db_engine and ai_client from bot_data safely."""
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
    return engine, ai_client


# ==============================================================================
# Create Wish & Friend Selection
# ==============================================================================

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
    engine, _ = _get_engine_and_client(context)

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


# ==============================================================================
# Wish History & Review
# ==============================================================================

async def wish_history_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1
) -> None:
    """
    Display paginated wish history for the user. Accessible via /wishhistory,
    /wishes, or the Wish History menu.
    """
    effective_user = update.effective_user
    if not effective_user:
        return

    telegram_id = int(effective_user.id)
    engine, _ = _get_engine_and_client(context)

    try:
        data = await asyncio.to_thread(
            handle_get_user_wishes,
            telegram_id=telegram_id,
            page=page,
            page_size=5,
            engine=engine,
        )
    except UserNotRegisteredError:
        msg = UNREGISTERED_WISH_MESSAGE
        if update.callback_query:
            await _safe_edit_callback_message(update.callback_query, text=msg)
        elif update.message:
            await update.message.reply_text(text=msg, reply_markup=get_main_menu_keyboard())
        return

    items = data["items"]
    current_page = data["page"]
    total_pages = data["total_pages"]
    total_count = data["total_count"]

    if total_count == 0:
        empty_text = (
            "📜 Wish History\n\n"
            "You haven't generated any birthday wishes yet!\n\n"
            "Tap \"✨ Create Wish\" to generate your first personalized wish."
        )
        markup = get_wish_history_keyboard([], 1, 1)
        if update.callback_query:
            await _safe_edit_callback_message(update.callback_query, text=empty_text, reply_markup=markup)
        elif update.message:
            await update.message.reply_text(text=empty_text, reply_markup=markup)
        return

    text = (
        f"📜 Your Birthday Wishes (Page {current_page}/{total_pages})\n"
        f"Total wishes saved: {total_count}\n\n"
        "Select a wish below to view details, copy, edit, or approve:"
    )
    markup = get_wish_history_keyboard(items, current_page, total_pages)

    if update.callback_query:
        await _safe_edit_callback_message(update.callback_query, text=text, reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text=text, reply_markup=markup)


async def _render_wish_view(
    query: Any,
    wish: dict,
    status_prefix: Optional[str] = None,
) -> None:
    """Helper to render a wish with its appropriate keyboard."""
    wish_id = wish["wish_id"]
    status = wish["status"]
    style = wish["style"]
    name = wish["friend_name"]
    created_at = wish.get("created_at")

    display = format_wish_display_message(
        name=name,
        wish_text=wish["content"],
        style=style,
        status=status,
        created_at=created_at,
    )
    if status_prefix:
        display = f"{status_prefix}\n\n{display}"

    if status == WishStatus.APPROVED:
        markup = get_wish_approved_keyboard(wish_id)
    elif status == WishStatus.SENT:
        markup = get_wish_sent_keyboard(wish_id)
    else:
        markup = get_wish_draft_keyboard(wish_id, style)

    await _safe_edit_callback_message(query, text=display, reply_markup=markup)


# ==============================================================================
# Main Callback Query Router
# ==============================================================================

async def wish_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle inline callbacks for Stage 11 wish management:
    - wish_cancel: Return to main menu
    - wish_new: Open friend selection to create a new wish
    - wish_hist:<page>: View paginated history
    - wish_view:<wish_id>: View an existing wish
    - wish_approve:<wish_id>: Approve a draft wish
    - wish_send:<wish_id>: Send an approved wish to linked friend
    - wish_copy:<wish_id>: Display copyable code block
    - wish_styles:<wish_id_or_friend_id>: Show style options
    - wish_setstyle:<id>:<style>: Set style and regenerate
    - wish_select:<friend_id>: Initial wish generation for a friend
    - wish_regen:<id>:<style>: Regenerate wish with new variation
    - wish_noop: No-operation
    """
    query = update.callback_query
    if query is None or query.data is None:
        return

    effective_user = update.effective_user
    if not effective_user:
        await query.answer("Could not identify user.")
        return

    telegram_id = int(effective_user.id)
    engine, ai_client = _get_engine_and_client(context)
    data = query.data

    # 1. Noop
    if data == "wish_noop":
        await query.answer()
        return

    # 2. Return to Main Menu
    if data == "wish_cancel":
        await query.answer()
        await _safe_edit_callback_message(query, text="Returning to main menu...")
        if query.message:
            await query.message.reply_text(
                text="🎂 Main Menu",
                reply_markup=get_main_menu_keyboard(),
            )
        return

    # 3. Create New Wish from button
    if data == "wish_new":
        await query.answer()
        try:
            friends = await asyncio.to_thread(
                handle_get_friends, telegram_id=telegram_id, engine=engine
            )
            if not friends:
                await _safe_edit_callback_message(
                    query,
                    text=EMPTY_FRIENDS_WISH_MESSAGE,
                    reply_markup=get_main_menu_keyboard(),
                )
                return
            await _safe_edit_callback_message(
                query,
                text="✨ Create Birthday Wish\n\nSelect a friend to generate a personalized birthday wish:",
                reply_markup=get_wish_friend_selection_keyboard(friends),
            )
        except Exception as err:
            logger.error("Error opening friend selection: %s", err)
            await query.answer("Error loading friends.")
        return

    # 4. Wish History Pagination
    if data.startswith("wish_hist:"):
        page = parse_callback_page(data, "wish_hist:")
        if page is None:
            await query.answer("⚠️ Invalid page number.", show_alert=True)
            return
        await query.answer()
        await wish_history_command(update, context, page=page)
        return

    # 5. View Wish by ID
    if data.startswith("wish_view:"):
        wish_id = parse_callback_id(data, "wish_view:")
        if wish_id is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        try:
            wish = await asyncio.to_thread(
                handle_get_wish, telegram_id=telegram_id, wish_id=wish_id, engine=engine
            )
            await query.answer()
            await _render_wish_view(query, wish)
        except WishNotFoundError:
            await query.answer("Wish not found.")
            await _safe_edit_callback_message(
                query,
                text="⚠️ Wish not found or access denied.",
                reply_markup=get_wish_error_keyboard(),
            )
        except Exception as err:
            logger.error("Error viewing wish %d: %s", wish_id, err)
            await query.answer("Error opening wish.")
        return

    # 6. Approve Wish
    if data.startswith("wish_approve:"):
        wish_id = parse_callback_id(data, "wish_approve:")
        if wish_id is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        try:
            wish = await asyncio.to_thread(
                handle_approve_wish,
                telegram_id=telegram_id,
                wish_id=wish_id,
                engine=engine,
            )
            await query.answer("Wish approved!")
            await _render_wish_view(
                query,
                wish,
                status_prefix="✅ Wish approved! You can now send it to your friend or copy it.",
            )
        except WishNotFoundError:
            await query.answer("Wish not found or access denied.")
            await _safe_edit_callback_message(
                query,
                text="⚠️ Wish not found or access denied.",
                reply_markup=get_wish_error_keyboard(),
            )
        except Exception as err:
            logger.error("Error approving wish %d: %s", wish_id, err)
            await query.answer("Could not approve wish.")
        return

    # 7. Send Wish
    if data.startswith("wish_send:"):
        wish_id = parse_callback_id(data, "wish_send:")
        if wish_id is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        try:
            res = await handle_send_wish(
                telegram_id=telegram_id,
                wish_id=wish_id,
                bot=context.bot,
                engine=engine,
            )
            await query.answer()

            if res.get("sent"):
                wish = await asyncio.to_thread(
                    handle_get_wish, telegram_id=telegram_id, wish_id=wish_id, engine=engine
                )
                await _render_wish_view(
                    query,
                    wish,
                    status_prefix=f"🚀 Wish successfully sent to {res['friend_name']} via Telegram!",
                )
            elif res.get("reason") == "unlinked":
                unlinked_msg = (
                    f"⚠️ {res['friend_name']} does not have a linked Telegram account.\n\n"
                    "You can tap \"📋 Copy Wish\" below to copy the message and send it to them directly!"
                )
                await _safe_edit_callback_message(
                    query,
                    text=unlinked_msg,
                    reply_markup=get_copy_view_keyboard(wish_id),
                )
            else:
                fail_msg = (
                    f"⚠️ Could not deliver message to {res['friend_name']} via Telegram.\n"
                    "They may not have started the bot or may have blocked messages.\n\n"
                    "Please tap \"📋 Copy Wish\" to send it manually."
                )
                await _safe_edit_callback_message(
                    query,
                    text=fail_msg,
                    reply_markup=get_copy_view_keyboard(wish_id),
                )
        except WishNotApprovedError:
            await query.answer("Please approve the wish before sending.", show_alert=True)
            await _safe_edit_callback_message(
                query,
                text="⚠️ Only approved wishes can be sent. Please approve the wish first.",
                reply_markup=get_wish_draft_keyboard(wish_id),
            )
        except WishNotFoundError:
            await query.answer("Wish not found.")
            await _safe_edit_callback_message(
                query,
                text="⚠️ Wish not found or access denied.",
                reply_markup=get_wish_error_keyboard(),
            )
        except Exception as err:
            logger.error("Error sending wish %d: %s", wish_id, err)
            await query.answer("Error sending wish.")
        return

    # 8. Copy Wish
    if data.startswith("wish_copy:"):
        wish_id = parse_callback_id(data, "wish_copy:")
        if wish_id is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        try:
            wish = await asyncio.to_thread(
                handle_get_wish, telegram_id=telegram_id, wish_id=wish_id, engine=engine
            )
            await query.answer()
            content = wish["content"]
            name = wish["friend_name"]
            copy_msg = (
                f"📋 Copyable Wish for {name}:\n\n"
                f"Tap the message below to copy to clipboard:\n\n"
                f"```{content}```"
            )
            await _safe_edit_callback_message(
                query,
                text=copy_msg,
                reply_markup=get_copy_view_keyboard(wish_id),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as err:
            logger.error("Error displaying copyable wish %d: %s", wish_id, err)
            await query.answer("Error preparing copyable text.")
        return

    # 9. Show Style Picker
    if data.startswith("wish_styles:"):
        id_val = parse_callback_id(data, "wish_styles:")
        if id_val is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        await query.answer()
        # Determine whether id is wish_id or friend_id
        is_wish = True
        friend_name = "Friend"
        try:
            wish = await asyncio.to_thread(
                handle_get_wish, telegram_id=telegram_id, wish_id=id_val, engine=engine
            )
            friend_name = wish["friend_name"]
        except Exception:
            is_wish = False
            try:
                prof = await asyncio.to_thread(
                    handle_get_friend_profile, telegram_id=telegram_id, friend_id=id_val, engine=engine
                )
                friend_name = prof["name"]
            except Exception:
                pass

        await _safe_edit_callback_message(
            query,
            text=f"🎨 Select a wish style for {friend_name}:",
            reply_markup=get_wish_styles_keyboard(id_val, is_wish_id=is_wish),
        )
        return

    # 10. Wish Generation (initial select, regen, or setstyle)
    friend_id: Optional[int] = None
    existing_wish_id: Optional[int] = None
    style: Optional[str] = None
    seed: Optional[int] = None

    if data.startswith("wish_select:"):
        friend_id = parse_callback_id(data, "wish_select:")
        if friend_id is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        existing_wish_id = None
        style = None

    elif data.startswith("wish_regen:"):
        parsed = parse_callback_id_and_value(data, "wish_regen:")
        if parsed is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        id_val, raw_style = parsed
        style = raw_style if raw_style != "None" else None
        seed = random.randint(1000, 99999)

        # Check if id_val is an existing wish
        try:
            w = await asyncio.to_thread(
                handle_get_wish, telegram_id=telegram_id, wish_id=id_val, engine=engine
            )
            existing_wish_id = id_val
            friend_id = w["friend_id"]
        except Exception:
            # Fallback if id_val was friend_id
            friend_id = id_val
            existing_wish_id = None

    elif data.startswith("wish_setstyle:"):
        parsed = parse_callback_id_and_value(data, "wish_setstyle:")
        if parsed is None:
            await query.answer("⚠️ Invalid or expired request.", show_alert=True)
            return
        id_val, style = parsed
        seed = random.randint(1000, 99999)

        try:
            w = await asyncio.to_thread(
                handle_get_wish, telegram_id=telegram_id, wish_id=id_val, engine=engine
            )
            existing_wish_id = id_val
            friend_id = w["friend_id"]
        except Exception:
            friend_id = id_val
            existing_wish_id = None

    if friend_id is None:
        await query.answer("⚠️ Unrecognized option.")
        return

    # Check friend existence & ownership
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
        wish_record = await create_or_regenerate_wish(
            telegram_id=telegram_id,
            friend_id=friend_id,
            existing_wish_id=existing_wish_id,
            style=style,
            variation_seed=seed,
            engine=engine,
            ai_client=ai_client,
        )

        display_text = format_wish_display_message(
            name=profile["name"],
            wish_text=wish_record["wish"],
            style=wish_record["style"],
            status=wish_record.get("status", WishStatus.DRAFT),
            created_at=wish_record.get("created_at"),
        )
        await _safe_edit_callback_message(
            query,
            text=display_text,
            reply_markup=get_wish_draft_keyboard(wish_record["wish_id"], wish_record["style"]),
        )

    except MissingApiKeyError:
        logger.warning("AI Wish generation requested with missing API key")
        await _safe_edit_callback_message(
            query,
            text=MISSING_API_KEY_MESSAGE,
            reply_markup=get_wish_error_keyboard(existing_wish_id or friend_id, is_wish_id=bool(existing_wish_id)),
        )
    except AIServiceUnavailableError:
        logger.warning("AI Wish generation temporary high demand (HTTP 503) for friend %d", friend_id)
        await _safe_edit_callback_message(
            query,
            text=HIGH_DEMAND_MESSAGE,
            reply_markup=get_wish_error_keyboard(existing_wish_id or friend_id, is_wish_id=bool(existing_wish_id)),
        )
    except AIQuotaExhaustedError:
        logger.warning("AI Wish generation quota exhausted for friend %d", friend_id)
        await _safe_edit_callback_message(
            query,
            text=QUOTA_EXHAUSTED_MESSAGE,
            reply_markup=get_wish_error_keyboard(existing_wish_id or friend_id, is_wish_id=bool(existing_wish_id)),
        )
    except AIRateLimitError:
        await _safe_edit_callback_message(
            query,
            text=RATE_LIMIT_MESSAGE,
            reply_markup=get_wish_error_keyboard(existing_wish_id or friend_id, is_wish_id=bool(existing_wish_id)),
        )
    except AITimeoutError:
        await _safe_edit_callback_message(
            query,
            text=TIMEOUT_MESSAGE,
            reply_markup=get_wish_error_keyboard(existing_wish_id or friend_id, is_wish_id=bool(existing_wish_id)),
        )
    except (AIResponseError, AIError) as err:
        logger.error("AI error generating wish for friend %d: %s", friend_id, err)
        await _safe_edit_callback_message(
            query,
            text=GENERIC_AI_ERROR_MESSAGE,
            reply_markup=get_wish_error_keyboard(existing_wish_id or friend_id, is_wish_id=bool(existing_wish_id)),
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
            reply_markup=get_wish_error_keyboard(existing_wish_id or friend_id, is_wish_id=bool(existing_wish_id)),
        )


# ==============================================================================
# Manual Wish Editing Flow (ConversationHandler)
# ==============================================================================

async def wish_edit_start_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Entry point for editing a wish. Triggered by '✏️ Edit Wish' button (wish_edit:<wish_id>).
    """
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END

    effective_user = update.effective_user
    if not effective_user:
        await query.answer("Could not identify user.")
        return ConversationHandler.END

    telegram_id = int(effective_user.id)
    engine, _ = _get_engine_and_client(context)

    wish_id = parse_callback_id(query.data, "wish_edit:")
    if wish_id is None:
        await query.answer("⚠️ Invalid or expired request.", show_alert=True)
        return ConversationHandler.END

    try:
        wish = await asyncio.to_thread(
            handle_get_wish, telegram_id=telegram_id, wish_id=wish_id, engine=engine
        )
    except WishNotFoundError:
        await query.answer("Wish not found.")
        await _safe_edit_callback_message(
            query,
            text="⚠️ Wish not found or access denied.",
            reply_markup=get_wish_error_keyboard(),
        )
        return ConversationHandler.END

    await query.answer()
    context.user_data["editing_wish_id"] = wish_id

    edit_prompt = (
        f"✏️ Edit Birthday Wish for {wish['friend_name']}\n\n"
        "Current text:\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{wish['content']}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Please send your revised birthday wish text below.\n"
        "Or tap \"❌ Cancel Edit\" to keep the current wish unchanged."
    )
    await _safe_edit_callback_message(
        query,
        text=edit_prompt,
        reply_markup=get_wish_cancel_edit_keyboard(wish_id),
    )
    return WAITING_FOR_WISH_EDIT_TEXT


async def wish_edit_text_received(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Handle user's revised wish text submission.
    Validates non-empty input, updates the database record, and resets status to DRAFT.
    """
    if update.message is None or not update.message.text:
        return WAITING_FOR_WISH_EDIT_TEXT

    telegram_id = int(update.effective_user.id)
    engine, _ = _get_engine_and_client(context)
    wish_id = context.user_data.get("editing_wish_id")

    if not wish_id:
        await update.message.reply_text(
            text="⚠️ No active wish edit session found. Returning to main menu.",
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    revised_text = update.message.text.strip()
    if not revised_text:
        await update.message.reply_text(
            text="⚠️ Wish text cannot be empty.\n\nPlease enter your revised message or tap ❌ Cancel Edit below:",
            reply_markup=get_wish_cancel_edit_keyboard(wish_id),
        )
        return WAITING_FOR_WISH_EDIT_TEXT

    try:
        wish = await asyncio.to_thread(
            handle_edit_wish,
            telegram_id=telegram_id,
            wish_id=wish_id,
            new_content=revised_text,
            engine=engine,
        )
    except EmptyWishContentError:
        await update.message.reply_text(
            text="⚠️ Wish text cannot be empty.\n\nPlease enter your revised message or tap ❌ Cancel Edit below:",
            reply_markup=get_wish_cancel_edit_keyboard(wish_id),
        )
        return WAITING_FOR_WISH_EDIT_TEXT
    except WishNotFoundError:
        await update.message.reply_text(
            text="⚠️ Wish not found or access denied.",
            reply_markup=get_main_menu_keyboard(),
        )
        context.user_data.pop("editing_wish_id", None)
        return ConversationHandler.END

    context.user_data.pop("editing_wish_id", None)

    display = format_wish_display_message(
        name=wish["friend_name"],
        wish_text=wish["content"],
        style=wish["style"],
        status=wish["status"],
        created_at=wish.get("created_at"),
    )
    success_text = f"✅ Birthday wish updated successfully!\n\n{display}"
    await update.message.reply_text(
        text=success_text,
        reply_markup=get_wish_draft_keyboard(wish_id, wish["style"]),
    )
    return ConversationHandler.END


async def wish_edit_cancel_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Handle cancellation of wish edit via inline button.
    Restores original wish view without altering content.
    """
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END

    effective_user = update.effective_user
    if not effective_user:
        await query.answer()
        return ConversationHandler.END

    telegram_id = int(effective_user.id)
    engine, _ = _get_engine_and_client(context)

    wish_id = parse_callback_id(query.data, "wish_edit_cancel:")
    if wish_id is None and context.user_data:
        saved_id = context.user_data.get("editing_wish_id")
        if isinstance(saved_id, int) and saved_id > 0:
            wish_id = saved_id

    if context.user_data:
        context.user_data.pop("editing_wish_id", None)

    await query.answer("Editing cancelled.")
    if wish_id:
        try:
            wish = await asyncio.to_thread(
                handle_get_wish, telegram_id=telegram_id, wish_id=wish_id, engine=engine
            )
            await _render_wish_view(query, wish, status_prefix="❌ Editing cancelled. Wish kept unchanged.")
            return ConversationHandler.END
        except Exception:
            pass

    await _safe_edit_callback_message(query, text="Editing cancelled. Returning to main menu...")
    if query.message:
        await query.message.reply_text(text="🎂 Main Menu", reply_markup=get_main_menu_keyboard())
    return ConversationHandler.END


async def wish_edit_cancel_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle /cancel command while in edit mode."""
    wish_id = context.user_data.pop("editing_wish_id", None)
    telegram_id = int(update.effective_user.id) if update.effective_user else 0
    engine, _ = _get_engine_and_client(context)

    if wish_id:
        try:
            wish = await asyncio.to_thread(
                handle_get_wish, telegram_id=telegram_id, wish_id=wish_id, engine=engine
            )
            display = format_wish_display_message(
                name=wish["friend_name"],
                wish_text=wish["content"],
                style=wish["style"],
                status=wish["status"],
                created_at=wish.get("created_at"),
            )
            await update.message.reply_text(
                text=f"❌ Editing cancelled. Wish kept unchanged.\n\n{display}",
                reply_markup=get_wish_draft_keyboard(wish_id, wish["style"]),
            )
            return ConversationHandler.END
        except Exception:
            pass

    if update.message:
        await update.message.reply_text(
            text="❌ Editing cancelled. Returning to main menu.",
            reply_markup=get_main_menu_keyboard(),
        )
    return ConversationHandler.END


def get_edit_wish_conversation_handler() -> ConversationHandler:
    """Build and return the ConversationHandler for the Manual Wish Edit flow."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=PTBUserWarning)
        return ConversationHandler(
            entry_points=[
                CallbackQueryHandler(wish_edit_start_callback, pattern=r"^wish_edit:(\d+)$"),
            ],
            states={
                WAITING_FOR_WISH_EDIT_TEXT: [
                    CallbackQueryHandler(wish_edit_cancel_callback, pattern=r"^wish_edit_cancel:(\d+)$"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, wish_edit_text_received),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", wish_edit_cancel_command),
                CallbackQueryHandler(wish_edit_cancel_callback, pattern=r"^wish_edit_cancel:(\d+)$"),
            ],
            allow_reentry=True,
            per_message=False,
        )


# ==============================================================================
# Friend Linking Helper (/linkfriend <friend_id> <telegram_id>)
# ==============================================================================

async def link_friend_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Link a friend's Telegram ID for testing or direct delivery.
    Usage: /linkfriend <friend_id> <telegram_id>
    """
    if update.message is None or not update.effective_user:
        return

    telegram_id = int(update.effective_user.id)
    engine, _ = _get_engine_and_client(context)

    args = context.args or []
    if len(args) != 2:
        await update.message.reply_text(
            text="ℹ️ Link Friend Telegram ID\n\n"
            "Usage: `/linkfriend <friend_id> <telegram_id>`\n"
            "Example: `/linkfriend 1 123456789`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        friend_id = int(args[0])
        target_telegram_id = int(args[1])
    except ValueError:
        await update.message.reply_text(text="⚠️ Friend ID and Telegram ID must both be numbers.")
        return

    try:
        res = await asyncio.to_thread(
            handle_link_friend_telegram,
            telegram_id=telegram_id,
            friend_id=friend_id,
            friend_telegram_id=target_telegram_id,
            engine=engine,
        )
        await update.message.reply_text(
            text=f"✅ Linked {res['name']} to Telegram ID `{target_telegram_id}`!\n"
            "Approved wishes can now be sent directly to this friend.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except FriendNotFoundError:
        await update.message.reply_text(text="⚠️ Friend not found or access denied.")
    except Exception as err:
        logger.error("Error linking friend telegram: %s", err)
        await update.message.reply_text(text="⚠️ An error occurred while linking friend.")
