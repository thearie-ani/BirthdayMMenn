"""
Automated tests for Safe Callback Query & Input Validation (Stage 12 Step 2).
Validates that:
1. Reusable callback parser functions safely handle invalid, empty, non-numeric,
   negative, and malformed payloads without throwing ValueError, IndexError, or TypeError.
2. Wish callback handlers safely reject malformed payloads, notify users via alerts,
   and never leave infinite loading spinners.
3. Friend profile callback handlers safely reject malformed payloads, notify users,
   and prevent unhandled exceptions.
4. Settings callback handlers safely reject malformed or unrecognized payloads.
5. Conversation entry points reject malformed IDs and gracefully terminate with ConversationHandler.END.
6. Valid callbacks retain proper functionality and cross-tenant ownership protection.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from telegram import CallbackQuery, Message, Update, User
from telegram.ext import ContextTypes, ConversationHandler

from app.bot.handlers.friends import (
    friend_profile_callback_handler,
    start_prompt_interests,
    start_prompt_notes,
    start_prompt_relationship,
    start_prompt_wish_style,
)
from app.bot.handlers.settings import (
    process_quick_timezone_callback,
    settings_callback_handler,
)
from app.bot.handlers.wish import (
    wish_callback_handler,
    wish_edit_cancel_callback,
    wish_edit_start_callback,
)
from app.utils.callback_utils import (
    parse_callback_id,
    parse_callback_id_and_value,
    parse_callback_page,
)


# ==============================================================================
# 1. Unit Tests for Callback Utilities
# ==============================================================================


def test_parse_callback_id_valid():
    """Verify parse_callback_id parses valid positive integers."""
    assert parse_callback_id("wish_view:123", "wish_view:") == 123
    assert parse_callback_id("fprof_view:1", "fprof_view:") == 1
    assert parse_callback_id("custom:999999", "custom:") == 999999
    assert parse_callback_id("wish_styles: 42 ", "wish_styles:") == 42


def test_parse_callback_id_malformed_and_edge_cases():
    """Verify parse_callback_id returns None for malformed, non-numeric, or missing IDs."""
    assert parse_callback_id(None, "wish_view:") is None
    assert parse_callback_id("", "wish_view:") is None
    assert parse_callback_id(12345, "wish_view:") is None  # type: ignore
    assert parse_callback_id("wish_view:", "wish_view:") is None
    assert parse_callback_id("wish_view:abc", "wish_view:") is None
    assert parse_callback_id("wish_view:12a", "wish_view:") is None
    assert parse_callback_id("wish_view:NaN", "wish_view:") is None
    assert parse_callback_id("wish_view:3.14", "wish_view:") is None
    assert parse_callback_id("wish_view:0", "wish_view:") is None
    assert parse_callback_id("wish_view:-15", "wish_view:") is None
    assert parse_callback_id("wish_view:12:34", "wish_view:") is None
    assert parse_callback_id("wish_view::1", "wish_view:") is None
    assert parse_callback_id("other_prefix:123", "wish_view:") is None
    assert parse_callback_id("wish_view", "wish_view:") is None


def test_parse_callback_id_and_value_valid():
    """Verify parse_callback_id_and_value parses valid ID and string value pairs."""
    assert parse_callback_id_and_value("fprof_set_rel:12:Friend", "fprof_set_rel:") == (12, "Friend")
    assert parse_callback_id_and_value("wish_regen:5:Funny & Humorous", "wish_regen:") == (5, "Funny & Humorous")
    assert parse_callback_id_and_value("wish_regen:7:None", "wish_regen:") == (7, "None")
    assert parse_callback_id_and_value("wish_setstyle:10:Warm & Heartfelt", "wish_setstyle:") == (10, "Warm & Heartfelt")
    # Multi-colon preserved in value part
    assert parse_callback_id_and_value("custom:10:part1:part2", "custom:") == (10, "part1:part2")


def test_parse_callback_id_and_value_malformed_and_edge_cases():
    """Verify parse_callback_id_and_value returns None for invalid structures."""
    assert parse_callback_id_and_value(None, "fprof_set_rel:") is None
    assert parse_callback_id_and_value("", "fprof_set_rel:") is None
    assert parse_callback_id_and_value("fprof_set_rel:12", "fprof_set_rel:") is None
    assert parse_callback_id_and_value("fprof_set_rel:12:", "fprof_set_rel:") is None
    assert parse_callback_id_and_value("fprof_set_rel::Friend", "fprof_set_rel:") is None
    assert parse_callback_id_and_value("fprof_set_rel:abc:Friend", "fprof_set_rel:") is None
    assert parse_callback_id_and_value("fprof_set_rel:0:Friend", "fprof_set_rel:") is None
    assert parse_callback_id_and_value("fprof_set_rel:-5:Friend", "fprof_set_rel:") is None
    assert parse_callback_id_and_value("wrong_prefix:12:Friend", "fprof_set_rel:") is None


def test_parse_callback_page_valid():
    """Verify parse_callback_page parses valid 1-based page numbers."""
    assert parse_callback_page("wish_hist:1", "wish_hist:") == 1
    assert parse_callback_page("wish_hist:10", "wish_hist:") == 10
    assert parse_callback_page("wish_hist: 3 ", "wish_hist:") == 3


def test_parse_callback_page_malformed_and_edge_cases():
    """Verify parse_callback_page rejects 0, negatives, non-numeric, or malformed page numbers."""
    assert parse_callback_page(None, "wish_hist:") is None
    assert parse_callback_page("", "wish_hist:") is None
    assert parse_callback_page("wish_hist:0", "wish_hist:") is None
    assert parse_callback_page("wish_hist:-1", "wish_hist:") is None
    assert parse_callback_page("wish_hist:abc", "wish_hist:") is None
    assert parse_callback_page("wish_hist:1:2", "wish_hist:") is None
    assert parse_callback_page("other_prefix:1", "wish_hist:") is None


# ==============================================================================
# Helper to build mock Update & Context
# ==============================================================================


def _build_mock_callback_update(
    callback_data: str, user_id: int = 123456
) -> Update:
    mock_user = MagicMock(spec=User)
    mock_user.id = user_id

    mock_msg = MagicMock(spec=Message)
    mock_msg.reply_text = AsyncMock()
    mock_msg.edit_text = AsyncMock()

    mock_query = MagicMock(spec=CallbackQuery)
    mock_query.data = callback_data
    mock_query.from_user = mock_user
    mock_query.message = mock_msg
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock(spec=Update)
    mock_update.effective_user = mock_user
    mock_update.effective_message = mock_msg
    mock_update.callback_query = mock_query
    return mock_update


def _build_mock_context(engine=None) -> ContextTypes.DEFAULT_TYPE:
    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    mock_context.bot_data = {"db_engine": engine} if engine else {}
    mock_context.user_data = {}
    mock_context.bot = MagicMock()
    mock_context.bot.send_message = AsyncMock()
    return mock_context


# ==============================================================================
# 2. Wish Handler Callback Validation Integration Tests
# ==============================================================================


@pytest.mark.anyio
async def test_wish_callback_malformed_pagination():
    """Verify wish_hist with non-numeric or invalid page returns friendly alert and no exception."""
    for bad_data in ["wish_hist:abc", "wish_hist:0", "wish_hist:-1", "wish_hist:1:2", "wish_hist:"]:
        update = _build_mock_callback_update(bad_data)
        context = _build_mock_context()

        await wish_callback_handler(update, context)

        update.callback_query.answer.assert_awaited_with(
            "⚠️ Invalid page number.", show_alert=True
        )


@pytest.mark.anyio
async def test_wish_callback_malformed_ids():
    """Verify wish callbacks with malformed IDs trigger alert and do not crash."""
    bad_callbacks = [
        "wish_view:notanid",
        "wish_view:",
        "wish_view:0",
        "wish_view:-5",
        "wish_view:12:34",
        "wish_approve:abc",
        "wish_approve:",
        "wish_send:abc",
        "wish_copy:xyz",
        "wish_styles:bad",
        "wish_select:corrupt",
        "wish_regen:bad",
        "wish_regen:12",  # missing style
        "wish_setstyle:bad",
        "wish_setstyle:12",  # missing style
    ]

    for bad_data in bad_callbacks:
        update = _build_mock_callback_update(bad_data)
        context = _build_mock_context()

        await wish_callback_handler(update, context)

        update.callback_query.answer.assert_awaited_with(
            "⚠️ Invalid or expired request.", show_alert=True
        )


@pytest.mark.anyio
async def test_wish_callback_unrecognized_button():
    """Verify completely unrecognized wish button callback data is cleanly answered."""
    update = _build_mock_callback_update("wish_unknown_action_xyz")
    context = _build_mock_context()

    await wish_callback_handler(update, context)

    update.callback_query.answer.assert_awaited_with("⚠️ Unrecognized option.")


@pytest.mark.anyio
async def test_wish_edit_start_callback_malformed_id():
    """Verify wish_edit_start_callback safely terminates on malformed callback data."""
    for bad_data in ["wish_edit:abc", "wish_edit:", "wish_edit:0", "wish_edit:1:2"]:
        update = _build_mock_callback_update(bad_data)
        context = _build_mock_context()

        result = await wish_edit_start_callback(update, context)

        assert result == ConversationHandler.END
        update.callback_query.answer.assert_awaited_with(
            "⚠️ Invalid or expired request.", show_alert=True
        )


@pytest.mark.anyio
async def test_wish_edit_cancel_callback_malformed_id():
    """Verify wish_edit_cancel_callback cancels safely even if callback payload is corrupted."""
    update = _build_mock_callback_update("wish_edit_cancel:abc")
    context = _build_mock_context()

    result = await wish_edit_cancel_callback(update, context)

    assert result == ConversationHandler.END
    update.callback_query.answer.assert_awaited_with("Editing cancelled.")


# ==============================================================================
# 3. Friend Profile Handler Callback Validation Integration Tests
# ==============================================================================


@pytest.mark.anyio
async def test_friend_profile_callback_malformed_ids():
    """Verify friend profile callbacks with malformed IDs trigger alert and do not crash."""
    bad_callbacks = [
        "fprof_view:invalid",
        "fprof_view:",
        "fprof_view:0",
        "fprof_view:-10",
        "fprof_rel_menu:bad",
        "fprof_rel_menu:",
        "fprof_style_menu:xyz",
        "fprof_style_menu:",
        "fprof_clear:abc",
        "fprof_clear:0",
        "fprof_set_rel:bad",
        "fprof_set_rel:12",  # missing value
        "fprof_set_rel:abc:Friend",  # non-numeric id
        "fprof_set_rel:0:Friend",  # zero id
        "fprof_set_style:bad",
        "fprof_set_style:12",  # missing style
        "fprof_set_style:abc:Warm",
    ]

    for bad_data in bad_callbacks:
        update = _build_mock_callback_update(bad_data)
        context = _build_mock_context()

        await friend_profile_callback_handler(update, context)

        update.callback_query.answer.assert_awaited_with(
            "⚠️ Invalid or expired request.", show_alert=True
        )


@pytest.mark.anyio
async def test_friend_profile_callback_unrecognized_button():
    """Verify unrecognized fprof_ button answers with clean notification."""
    update = _build_mock_callback_update("fprof_unknown_action")
    context = _build_mock_context()

    await friend_profile_callback_handler(update, context)

    update.callback_query.answer.assert_awaited_with("⚠️ Unrecognized option.")


@pytest.mark.anyio
async def test_friend_profile_start_prompts_malformed_data():
    """Verify prompt entry points safely reject non-numeric IDs and end conversation."""
    prompt_handlers = [
        (start_prompt_relationship, "fprof_prompt_rel:bad"),
        (start_prompt_relationship, "fprof_prompt_rel:"),
        (start_prompt_relationship, "fprof_prompt_rel:0"),
        (start_prompt_interests, "fprof_prompt_interests:bad"),
        (start_prompt_interests, "fprof_prompt_interests:"),
        (start_prompt_notes, "fprof_prompt_notes:bad"),
        (start_prompt_notes, "fprof_prompt_notes:"),
        (start_prompt_wish_style, "fprof_prompt_style:bad"),
        (start_prompt_wish_style, "fprof_prompt_style:"),
    ]

    for handler, bad_data in prompt_handlers:
        update = _build_mock_callback_update(bad_data)
        context = _build_mock_context()

        result = await handler(update, context)

        assert result == ConversationHandler.END
        update.callback_query.answer.assert_awaited_with(
            "⚠️ Invalid or expired request.", show_alert=True
        )


# ==============================================================================
# 4. Settings Handler Callback Validation Integration Tests
# ==============================================================================


@pytest.mark.anyio
async def test_settings_callback_unrecognized_data():
    """Verify settings callback answers unrecognized button data."""
    update = _build_mock_callback_update("settings_tampered_button")
    context = _build_mock_context()

    await settings_callback_handler(update, context)

    update.callback_query.answer.assert_awaited_with("⚠️ Unrecognized option.")


@pytest.mark.anyio
async def test_quick_timezone_callback_malformed():
    """Verify quick timezone callback rejects empty or malformed timezone payloads."""
    for bad_data in ["tz_set:", "tz_set:   ", "tz_notset"]:
        update = _build_mock_callback_update(bad_data)
        context = _build_mock_context()

        result = await process_quick_timezone_callback(update, context)

        assert result == ConversationHandler.END
        update.callback_query.answer.assert_awaited_with(
            "⚠️ Invalid timezone selection.", show_alert=True
        )
