"""
Global Application Error Handler for BirthdayMate.
Catches unexpected exceptions across Telegram handlers, prevents unresponsive UI,
answers callback queries safely, and logs diagnostic details without exposing secrets.
"""
import logging
from typing import Optional
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.config.settings import get_ai_api_key, get_bot_token

logger = logging.getLogger(__name__)

USER_FACING_ERROR_MESSAGE = "⚠️ Something went wrong. Please try again or return to /start."


def sanitize_sensitive_data(text: str) -> str:
    """
    Remove or mask known sensitive secrets (bot token, AI API key) from strings.
    """
    if not text:
        return ""

    sanitized = str(text)

    try:
        token = get_bot_token()
        if token and len(token) > 6:
            sanitized = sanitized.replace(token, "[REDACTED_BOT_TOKEN]")
    except Exception:
        pass

    try:
        api_key = get_ai_api_key()
        if api_key and len(api_key) > 6:
            sanitized = sanitized.replace(api_key, "[REDACTED_AI_KEY]")
    except Exception:
        pass

    return sanitized


async def global_error_handler(
    update: Optional[object], context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Central error handler registered via application.add_error_handler().
    Catches unhandled handler exceptions, logs safe diagnostic output,
    and returns a user-friendly error response to prevent frozen client UI.
    """
    try:
        error = getattr(context, "error", None)
        error_name = type(error).__name__ if error else "UnknownException"
        error_msg = sanitize_sensitive_data(str(error)) if error else "No exception details"

        update_id = getattr(update, "update_id", None) if update else None
        user_id = None
        if isinstance(update, Update) and update.effective_user:
            user_id = update.effective_user.id

        # Log safely without leaking tokens or headers
        logger.error(
            "Unhandled Telegram exception [%s]: %s (update_id=%s, user_id=%s)",
            error_name,
            error_msg,
            update_id,
            user_id,
        )

        if not isinstance(update, Update):
            # Update was None or not a standard Telegram Update (e.g. network polling error)
            return

        # 1. Handle Callback Query updates (Stop endless loading spinner)
        if update.callback_query is not None:
            try:
                await update.callback_query.answer(
                    text=USER_FACING_ERROR_MESSAGE,
                    show_alert=True,
                )
            except BadRequest as br_err:
                # Query might be too old or already answered
                logger.debug("Failed to answer callback query in error handler: %s", br_err)
            except Exception as cb_err:
                logger.warning("Error answering callback query in error handler: %s", cb_err)
            return

        # 2. Handle Message updates (Send single polite user-facing error response)
        if update.effective_message is not None:
            try:
                await update.effective_message.reply_text(text=USER_FACING_ERROR_MESSAGE)
            except Exception as msg_err:
                logger.warning("Failed to send error notification message to user: %s", msg_err)
            return

    except Exception as fatal_handler_err:
        # Prevent recursive loop or crashes within the error handler itself
        logger.critical(
            "Critical failure inside global_error_handler: %s",
            fatal_handler_err,
        )


async def dev_sim_error_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Development-only command to trigger a simulated exception for testing
    the global error handler. Only active when ALLOW_DEV_ERROR_SIMULATION is enabled.
    """
    raise RuntimeError("Simulated development exception for error handler testing.")
