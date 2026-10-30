"""
Automated tests for Global Application Error Handling (Stage 12 Step 1).
Verifies safe logging, secret redaction, callback answering, message fallback responses,
and error loop prevention.
"""
import logging
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from telegram import CallbackQuery, Message, Update, User
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, ContextTypes

from app.bot.handlers.error_handler import (
    USER_FACING_ERROR_MESSAGE,
    dev_sim_error_command,
    global_error_handler,
    sanitize_sensitive_data,
)
from app.main import create_bot_application


@pytest.mark.anyio
async def test_global_error_handler_message_exception():
    """Verify error handler sends user-facing error message for unhandled message exceptions."""
    mock_msg = MagicMock(spec=Message)
    mock_msg.reply_text = AsyncMock()

    mock_user = MagicMock(spec=User)
    mock_user.id = 123456789

    mock_update = MagicMock(spec=Update)
    mock_update.update_id = 999
    mock_update.callback_query = None
    mock_update.effective_message = mock_msg
    mock_update.effective_user = mock_user

    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    mock_context.error = ValueError("Unexpected computation failure in message handler")

    await global_error_handler(mock_update, mock_context)

    mock_msg.reply_text.assert_awaited_once_with(text=USER_FACING_ERROR_MESSAGE)


@pytest.mark.anyio
async def test_global_error_handler_callback_query_exception():
    """Verify error handler answers callback query to prevent endless loading spinner."""
    mock_query = MagicMock(spec=CallbackQuery)
    mock_query.answer = AsyncMock()

    mock_msg = MagicMock(spec=Message)
    mock_msg.reply_text = AsyncMock()

    mock_user = MagicMock(spec=User)
    mock_user.id = 123456789

    mock_update = MagicMock(spec=Update)
    mock_update.update_id = 888
    mock_update.callback_query = mock_query
    mock_update.effective_message = mock_msg
    mock_update.effective_user = mock_user

    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    mock_context.error = RuntimeError("Unexpected failure during inline button callback")

    await global_error_handler(mock_update, mock_context)

    # Must answer query so spinner stops
    mock_query.answer.assert_awaited_once_with(
        text=USER_FACING_ERROR_MESSAGE,
        show_alert=True,
    )
    # Must NOT send an extra duplicate chat message
    mock_msg.reply_text.assert_not_called()


@pytest.mark.anyio
async def test_global_error_handler_callback_query_bad_request_swallowed():
    """Verify handler swallows BadRequest when answering an expired or already-answered query."""
    mock_query = MagicMock(spec=CallbackQuery)
    mock_query.answer = AsyncMock(side_effect=BadRequest("Query is too old and response timeout expired"))

    mock_update = MagicMock(spec=Update)
    mock_update.update_id = 777
    mock_update.callback_query = mock_query
    mock_update.effective_message = None
    mock_update.effective_user = None

    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    mock_context.error = RuntimeError("Database connection timed out")

    # Should complete safely without re-raising BadRequest
    await global_error_handler(mock_update, mock_context)


@pytest.mark.anyio
async def test_global_error_handler_safe_logging_no_secrets(caplog):
    """Verify error logger redacts bot tokens and AI API keys from log records."""
    fake_token = "1234567890:AAFakeBotToken_SecretABC"
    fake_ai_key = "AIzaSyFakeSecretAPIKey_12345678"

    error_with_secrets = Exception(
        f"Failed request to https://api.telegram.org/bot{fake_token}/sendMessage?key={fake_ai_key}"
    )

    mock_update = MagicMock(spec=Update)
    mock_update.update_id = 111
    mock_update.callback_query = None
    mock_update.effective_message = None
    mock_update.effective_user = None

    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    mock_context.error = error_with_secrets

    with patch("app.bot.handlers.error_handler.get_bot_token", return_value=fake_token), \
         patch("app.bot.handlers.error_handler.get_ai_api_key", return_value=fake_ai_key):
        with caplog.at_level(logging.ERROR):
            await global_error_handler(mock_update, mock_context)

    # Neither secret should appear in logs
    assert fake_token not in caplog.text
    assert fake_ai_key not in caplog.text
    assert "[REDACTED_BOT_TOKEN]" in caplog.text
    assert "[REDACTED_AI_KEY]" in caplog.text


@pytest.mark.anyio
async def test_global_error_handler_update_none_or_non_standard():
    """Verify error handler runs safely when update is None (e.g. polling network error)."""
    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    mock_context.error = ConnectionResetError("Telegram server reset connection during polling")

    # update is None
    await global_error_handler(None, mock_context)

    # update is arbitrary object
    await global_error_handler(object(), mock_context)


@pytest.mark.anyio
async def test_global_error_handler_internal_error_swallowed(caplog):
    """Verify error handler does not enter an unhandled exception loop if replying fails."""
    mock_msg = MagicMock(spec=Message)
    # Simulate network failure while trying to notify the user
    mock_msg.reply_text = AsyncMock(side_effect=Exception("Telegram connection broken"))

    mock_update = MagicMock(spec=Update)
    mock_update.callback_query = None
    mock_update.effective_message = mock_msg
    mock_update.effective_user = None

    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    mock_context.error = ValueError("Original handler error")

    # Must not raise
    with caplog.at_level(logging.WARNING):
        await global_error_handler(mock_update, mock_context)

    assert "Failed to send error notification message to user" in caplog.text


@pytest.mark.anyio
async def test_dev_sim_error_command_raises():
    """Verify dev_sim_error_command raises RuntimeError for simulated error testing."""
    mock_update = MagicMock(spec=Update)
    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with pytest.raises(RuntimeError, match="Simulated development exception"):
        await dev_sim_error_command(mock_update, mock_context)


def test_sanitize_sensitive_data():
    """Verify sanitize_sensitive_data properly replaces tokens and keys."""
    raw = "Header: bot999888:SecretToken123 and x-goog-api-key: AIzaSySecretKeyXYZ"
    with patch("app.bot.handlers.error_handler.get_bot_token", return_value="999888:SecretToken123"), \
         patch("app.bot.handlers.error_handler.get_ai_api_key", return_value="AIzaSySecretKeyXYZ"):
        cleaned = sanitize_sensitive_data(raw)
        assert "SecretToken123" not in cleaned
        assert "AIzaSySecretKeyXYZ" not in cleaned
        assert "[REDACTED_BOT_TOKEN]" in cleaned
        assert "[REDACTED_AI_KEY]" in cleaned

    assert sanitize_sensitive_data("") == ""


def test_create_bot_application_registers_global_error_handler():
    """Verify create_bot_application registers the global_error_handler."""
    app = create_bot_application("123456:ABC-DEF-ValidFormatTestToken12345")
    # Verify error handlers list has global_error_handler
    assert global_error_handler in app.error_handlers
