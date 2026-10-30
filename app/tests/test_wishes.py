"""
Automated unit and integration tests for Stage 10: AI Birthday Wish Generation.
Verifies prompt construction, styles, Gemini client, business logic, error handling,
Telegram handlers, callbacks, and user data isolation without making real network/AI calls.
"""
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ConversationHandler

from app.ai.client import (
    AIError,
    AIQuotaExhaustedError,
    AIResponseError,
    AIRateLimitError,
    AIServiceUnavailableError,
    AITimeoutError,
    GeminiClient,
    MissingApiKeyError,
)
from app.ai.prompts import (
    STYLE_MAPPINGS,
    SUPPORTED_STYLES,
    SYSTEM_INSTRUCTION,
    build_wish_prompt,
    normalize_style,
)
from app.ai.wish_generator import WishGenerator
from app.bot.handlers.wish import (
    ACCESS_DENIED_MESSAGE,
    EMPTY_FRIENDS_WISH_MESSAGE,
    GENERIC_AI_ERROR_MESSAGE,
    HIGH_DEMAND_MESSAGE,
    MISSING_API_KEY_MESSAGE,
    QUOTA_EXHAUSTED_MESSAGE,
    RATE_LIMIT_MESSAGE,
    TIMEOUT_MESSAGE,
    UNREGISTERED_WISH_MESSAGE,
    create_wish_command,
    format_wish_display_message,
    wish_callback_handler,
)
from app.bot.keyboards.wish_menu import (
    get_wish_error_keyboard,
    get_wish_friend_selection_keyboard,
    get_wish_result_keyboard,
    get_wish_styles_keyboard,
)
from app.config.settings import get_ai_api_key, get_ai_model
from app.database.database import init_db
from app.services.friend_ser import (
    FriendNotFoundError,
    UserNotRegisteredError,
    add_friend_for_user,
    handle_update_friend_profile,
)
from app.services.profile_ser import register_or_update_user
from app.services.wish_ser import generate_wish_for_friend


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def thread_safe_engine():
    """Create an isolated in-memory SQLite database engine for async/thread safety."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def sample_user_and_friend(thread_safe_engine):
    """Seed a user and a friend with a completed profile."""
    from app.database.database import get_session

    with get_session(thread_safe_engine) as session:
        user = register_or_update_user(
            session=session,
            telegram_id=123456789,
            first_name="Alice",
            username="alice",
        )
        friend = add_friend_for_user(
            session=session,
            telegram_id=123456789,
            name="Charlie",
            birth_month=10,
            birth_day=25,
            birth_year=1995,
        )
        friend_id = friend.id

    # Add profile details
    handle_update_friend_profile(
        telegram_id=123456789,
        friend_id=friend_id,
        relationship="Best Friend",
        interests="Photography, Indie Rock, Hiking",
        notes="Helped me move to my new apartment last summer",
        wish_style="Warm & Heartfelt",
        engine=thread_safe_engine,
    )

    return {"telegram_id": 123456789, "friend_id": friend_id, "friend_name": "Charlie"}


# ==============================================================================
# 1. Configuration & Settings Tests
# ==============================================================================


def test_get_ai_api_key():
    """Verify get_ai_api_key returns None when unset or placeholder, and string when valid."""
    with patch.dict("os.environ", {"AI_API_KEY": ""}):
        assert get_ai_api_key() is None

    with patch.dict("os.environ", {"AI_API_KEY": "your_ai_api_key_here"}):
        assert get_ai_api_key() is None

    with patch.dict("os.environ", {"AI_API_KEY": "AIzaSyFakeSecretKey123"}):
        assert get_ai_api_key() == "AIzaSyFakeSecretKey123"


def test_get_ai_model():
    """Verify get_ai_model returns default gemini-3.8-flash or environment override."""
    with patch.dict("os.environ", {}, clear=True):
        assert get_ai_model() == "gemini-3.8-flash"

    with patch.dict("os.environ", {"GEMINI_MODEL": "gemini-2.0-flash"}):
        assert get_ai_model() == "gemini-2.0-flash"

    with patch.dict("os.environ", {"AI_MODEL": "gemini-1.5-flash"}, clear=True):
        assert get_ai_model() == "gemini-1.5-flash"


# ==============================================================================
# 2. Prompts, Styles & Normalization Tests
# ==============================================================================


def test_normalize_style():
    """Verify style mapping converts variations to canonical styles."""
    assert normalize_style(None) == "Friendly"
    assert normalize_style("") == "Friendly"
    assert normalize_style("warm & heartfelt") == "Warm and heartfelt"
    assert normalize_style("Warm & Heartfelt") == "Warm and heartfelt"
    assert normalize_style("funny & humorous") == "Funny"
    assert normalize_style("Poetic & Creative") == "Poetic"
    assert normalize_style("Formal & Polite") == "Formal"
    assert normalize_style("Casual & Friendly") == "Casual"
    assert normalize_style("Short and simple") == "Short and simple"
    assert normalize_style("custom sarcastic") == "Custom sarcastic"


def test_build_wish_prompt_all_fields():
    """Verify prompt incorporates name, relationship, interests, notes, and style."""
    prompt, sys_inst, style = build_wish_prompt(
        name="Elena",
        relationship="College Roommate",
        interests="Sci-Fi books, Baking",
        notes="We made late-night cinnamon rolls together",
        wish_style="Warm and heartfelt",
        variation_seed=42,
    )

    assert "Elena" in prompt
    assert "College Roommate" in prompt
    assert "Sci-Fi books, Baking" in prompt
    assert "late-night cinnamon rolls" in prompt
    assert "Warm and heartfelt" in prompt
    assert "Variation #42" in prompt
    assert style == "Warm and heartfelt"
    assert "Strict Truthfulness" in sys_inst
    assert "Never make up, invent, or assume" in sys_inst


def test_build_wish_prompt_missing_fields():
    """Verify prompt builds cleanly when relationship, interests, and notes are empty."""
    prompt, sys_inst, style = build_wish_prompt(
        name="Bob",
        relationship=None,
        interests="",
        notes=None,
        wish_style="Funny",
    )

    assert "Bob" in prompt
    assert "Relationship to Sender" not in prompt
    assert "Interests & Passions" not in prompt
    assert "Special Notes" not in prompt
    assert "Funny" in prompt
    assert style == "Funny"


@pytest.mark.parametrize(
    "style_name",
    [
        "Friendly",
        "Funny",
        "Short and simple",
        "Formal",
        "Warm and heartfelt",
        "Casual",
        "Poetic",
    ],
)
def test_all_supported_styles_produce_valid_prompts(style_name):
    """Verify all project-supported styles produce valid prompts and descriptions."""
    prompt, sys_inst, style = build_wish_prompt(name="Sam", wish_style=style_name)
    assert style == style_name
    assert style_name in prompt


def test_generate_another_distinct_creative_angles():
    """Verify that different variation seeds produce distinct creative angles in prompts."""
    prompt1, _, _ = build_wish_prompt(name="Alex", wish_style="Friendly", variation_seed=1)
    prompt2, _, _ = build_wish_prompt(name="Alex", wish_style="Friendly", variation_seed=2)
    prompt3, _, _ = build_wish_prompt(name="Alex", wish_style="Friendly", variation_seed=3)

    assert prompt1 != prompt2
    assert prompt2 != prompt3
    assert "Variation #1" in prompt1
    assert "Variation #2" in prompt2
    assert "Variation #3" in prompt3


# ==============================================================================
# 3. GeminiClient Tests (Mocked Network / No Real Calls)
# ==============================================================================


@pytest.mark.anyio
async def test_gemini_client_missing_api_key_raises():
    """Verify GeminiClient raises MissingApiKeyError if API key is empty or placeholder."""
    client_empty = GeminiClient(api_key="")
    with pytest.raises(MissingApiKeyError):
        await client_empty.generate_text("Hello")

    client_placeholder = GeminiClient(api_key="your_ai_api_key_here")
    with pytest.raises(MissingApiKeyError):
        await client_placeholder.generate_text("Hello")


@pytest.mark.anyio
async def test_gemini_client_successful_response():
    """Verify GeminiClient successfully parses standard Gemini 3.8 API response."""
    fake_response_data = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "Happy Birthday, Charlie! Wishing you a fantastic year filled with great photography adventures!"
                        }
                    ],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_error = False
    mock_resp.json.return_value = fake_response_data

    client = GeminiClient(api_key="fake-test-key", model="gemini-3.8-flash")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        result = await client.generate_text(
            prompt="Write a wish for Charlie",
            system_instruction="Be friendly",
        )

        assert "Happy Birthday, Charlie!" in result
        mock_post.assert_awaited_once()
        args, kwargs = mock_post.call_args
        assert "models/gemini-3.8-flash:generateContent" in args[0]
        assert kwargs["headers"]["x-goog-api-key"] == "fake-test-key"
        assert kwargs["json"]["contents"][0]["parts"][0]["text"] == "Write a wish for Charlie"

        # Assert no unsupported parameters exist in generationConfig
        gen_config = kwargs["json"].get("generationConfig", {})
        assert "temperature" not in gen_config
        assert "top_p" not in gen_config
        assert "top_k" not in gen_config
        assert gen_config.get("maxOutputTokens") == 600


@pytest.mark.anyio
async def test_gemini_client_http_404_model_not_found():
    """Verify HTTP 404 model not found raises informative AIResponseError."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.is_error = True
    mock_resp.text = "models/gemini-2.5-flash is not found for API version v1beta"

    client = GeminiClient(api_key="fake-test-key", model="gemini-2.5-flash")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        with pytest.raises(AIResponseError) as exc_info:
            await client.generate_text("Prompt")

        assert "HTTP 404" in str(exc_info.value)
        assert "gemini-2.5-flash" in str(exc_info.value)



@pytest.mark.anyio
async def test_gemini_client_503_recovery():
    """Verify GeminiClient automatically retries HTTP 503 and recovers upon success."""
    mock_resp_503 = MagicMock()
    mock_resp_503.status_code = 503
    mock_resp_503.is_error = True
    mock_resp_503.headers = {}
    mock_resp_503.text = "This model is currently experiencing high demand."

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.is_error = False
    mock_resp_200.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Recovered wish!"}]}}]
    }

    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="fake-test-key",
        max_retries=2,
        base_backoff=0.01,
        sleep_func=mock_sleep,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [mock_resp_503, mock_resp_200]

        result = await client.generate_text("Hello")

        assert result == "Recovered wish!"
        assert mock_post.await_count == 2
        mock_sleep.assert_awaited_once()


@pytest.mark.anyio
async def test_gemini_client_repeated_503_exhausts_retries():
    """Verify 503 persisting across all 2 retries raises AIServiceUnavailableError."""
    mock_resp_503 = MagicMock()
    mock_resp_503.status_code = 503
    mock_resp_503.is_error = True
    mock_resp_503.headers = {}
    mock_resp_503.text = "This model is currently experiencing high demand."

    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="fake-test-key",
        max_retries=2,
        base_backoff=0.01,
        sleep_func=mock_sleep,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [mock_resp_503, mock_resp_503, mock_resp_503]

        with pytest.raises(AIServiceUnavailableError) as exc_info:
            await client.generate_text("Hello")

        assert "high demand" in str(exc_info.value)
        # Exactly 1 initial + 2 retries = 3 calls
        assert mock_post.await_count == 3
        assert mock_sleep.await_count == 2


@pytest.mark.anyio
async def test_gemini_client_respects_retry_after_header():
    """Verify GeminiClient parses and respects Retry-After header on 503."""
    mock_resp_503 = MagicMock()
    mock_resp_503.status_code = 503
    mock_resp_503.is_error = True
    mock_resp_503.headers = {"Retry-After": "4"}
    mock_resp_503.text = "High demand"

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.is_error = False
    mock_resp_200.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Success after Retry-After!"}]}}]
    }

    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="fake-test-key",
        max_retries=2,
        max_retry_after_wait=10.0,
        sleep_func=mock_sleep,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [mock_resp_503, mock_resp_200]

        result = await client.generate_text("Hello")

        assert result == "Success after Retry-After!"
        mock_sleep.assert_awaited_once_with(4.0)


@pytest.mark.anyio
async def test_gemini_client_retry_after_capped_at_max():
    """Verify Retry-After exceeding max wait is clamped to max_retry_after_wait."""
    mock_resp_503 = MagicMock()
    mock_resp_503.status_code = 503
    mock_resp_503.is_error = True
    mock_resp_503.headers = {"Retry-After": "120"}
    mock_resp_503.text = "High demand"

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.is_error = False
    mock_resp_200.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Success!"}]}}]
    }

    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="fake-test-key",
        max_retries=2,
        base_backoff=1.0,
        max_retry_after_wait=10.0,
        sleep_func=mock_sleep,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [mock_resp_503, mock_resp_200]

        await client.generate_text("Hello")
        # 120s exceeds 10s max wait, so falls back to exponential backoff min(backoff, max_backoff)
        mock_sleep.assert_awaited_once_with(1.0)


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
async def test_gemini_client_permanent_errors_not_retried(status_code):
    """Verify permanent errors (400, 401, 403, 404) are NOT retried."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.is_error = True
    mock_resp.text = f"Permanent error {status_code}"

    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="fake-test-key",
        max_retries=2,
        sleep_func=mock_sleep,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        with pytest.raises((AIResponseError, MissingApiKeyError)):
            await client.generate_text("Hello")

        # Must NOT retry: called exactly once
        assert mock_post.await_count == 1
        mock_sleep.assert_not_awaited()


@pytest.mark.anyio
async def test_gemini_client_quota_exhausted_not_retried():
    """Verify 429 with quota exhaustion is recognized and NOT retried."""
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.is_error = True
    mock_resp.text = "Quota exceeded for quota metric 'GenerateContentRequestsPerDay'"

    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="fake-test-key",
        max_retries=2,
        sleep_func=mock_sleep,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        with pytest.raises(AIQuotaExhaustedError):
            await client.generate_text("Hello")

        assert mock_post.await_count == 1
        mock_sleep.assert_not_awaited()


@pytest.mark.anyio
async def test_gemini_client_transient_rate_limit_recovery():
    """Verify 429 burst rate limit is retried and recovers upon 200."""
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.is_error = True
    mock_resp_429.headers = {"Retry-After": "2"}
    mock_resp_429.text = "Too many requests. Please wait."

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.is_error = False
    mock_resp_200.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Wish after burst wait!"}]}}]
    }

    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="fake-test-key",
        max_retries=2,
        sleep_func=mock_sleep,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [mock_resp_429, mock_resp_200]

        result = await client.generate_text("Hello")

        assert result == "Wish after burst wait!"
        assert mock_post.await_count == 2
        mock_sleep.assert_awaited_once_with(2.0)


@pytest.mark.anyio
async def test_gemini_client_timeout_recovery():
    """Verify client retries on timeout and recovers."""
    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.is_error = False
    mock_resp_200.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Wish after timeout retry!"}]}}]
    }

    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="fake-test-key",
        max_retries=2,
        base_backoff=0.01,
        sleep_func=mock_sleep,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [
            httpx.TimeoutException("Read timeout"),
            mock_resp_200,
        ]

        result = await client.generate_text("Hello")

        assert result == "Wish after timeout retry!"
        assert mock_post.await_count == 2
        mock_sleep.assert_awaited_once()


@pytest.mark.anyio
async def test_gemini_client_rate_limit_429():
    """Verify HTTP 429 response is translated to AIRateLimitError."""
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.is_error = True
    mock_resp.text = "RESOURCE_EXHAUSTED"

    client = GeminiClient(api_key="fake-test-key")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        with pytest.raises(AIRateLimitError):
            await client.generate_text("Prompt")


def test_gemini_client_timeout_config():
    """Verify GeminiClient constructs granular httpx.Timeout settings."""
    client = GeminiClient(
        api_key="fake-key",
        connect_timeout=8.0,
        read_timeout=35.0,
        write_timeout=12.0,
        pool_timeout=4.0,
    )
    timeout = client.timeout_config
    assert timeout.connect == 8.0
    assert timeout.read == 35.0
    assert timeout.write == 12.0
    assert timeout.pool == 4.0


@pytest.mark.anyio
async def test_gemini_client_read_timeout_diagnostic_error():
    """Verify ReadTimeout raises AITimeoutError with specific ReadTimeout diagnostic message."""
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="fake-test-key",
        max_retries=1,
        sleep_func=mock_sleep,
    )

    with patch(
        "httpx.AsyncClient.post",
        side_effect=httpx.ReadTimeout("Read timed out"),
    ) as mock_post:
        with pytest.raises(AITimeoutError) as exc_info:
            await client.generate_text("Prompt")

        assert "ReadTimeout" in str(exc_info.value)
        assert mock_post.await_count == 2
        mock_sleep.assert_awaited_once()


@pytest.mark.anyio
async def test_gemini_client_connect_timeout_diagnostic_error():
    """Verify ConnectTimeout raises AITimeoutError with specific ConnectTimeout diagnostic message."""
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="fake-test-key",
        max_retries=1,
        sleep_func=mock_sleep,
    )

    with patch(
        "httpx.AsyncClient.post",
        side_effect=httpx.ConnectTimeout("Connect timed out"),
    ) as mock_post:
        with pytest.raises(AITimeoutError) as exc_info:
            await client.generate_text("Prompt")

        assert "ConnectTimeout" in str(exc_info.value)
        assert mock_post.await_count == 2
        mock_sleep.assert_awaited_once()


@pytest.mark.anyio
async def test_gemini_client_timeout():
    """Verify httpx.TimeoutException is translated to AITimeoutError."""
    client = GeminiClient(api_key="fake-test-key", max_retries=1, base_backoff=0.001)

    with patch(
        "httpx.AsyncClient.post",
        side_effect=httpx.TimeoutException("Read timed out"),
    ):
        with pytest.raises(AITimeoutError):
            await client.generate_text("Prompt")


@pytest.mark.anyio
async def test_gemini_client_http_500():
    """Verify HTTP 500 error is translated to AIResponseError."""
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.is_error = True
    mock_resp.text = "Internal Server Error"

    client = GeminiClient(api_key="fake-test-key")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        with pytest.raises(AIResponseError):
            await client.generate_text("Prompt")


@pytest.mark.anyio
async def test_gemini_client_empty_response():
    """Verify empty candidates or blank text raises AIResponseError."""
    client = GeminiClient(api_key="fake-test-key")

    mock_resp_empty_cand = MagicMock()
    mock_resp_empty_cand.status_code = 200
    mock_resp_empty_cand.is_error = False
    mock_resp_empty_cand.json.return_value = {"candidates": []}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp_empty_cand
        with pytest.raises(AIResponseError):
            await client.generate_text("Prompt")

    mock_resp_blank_text = MagicMock()
    mock_resp_blank_text.status_code = 200
    mock_resp_blank_text.is_error = False
    mock_resp_blank_text.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "   "}]}}]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp_blank_text
        with pytest.raises(AIResponseError):
            await client.generate_text("Prompt")


@pytest.mark.anyio
async def test_gemini_client_safety_block():
    """Verify finishReason SAFETY raises AIResponseError."""
    client = GeminiClient(api_key="fake-test-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_error = False
    mock_resp.json.return_value = {
        "candidates": [
            {
                "finishReason": "SAFETY",
                "content": {"parts": []},
            }
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIResponseError):
            await client.generate_text("Prompt")


# ==============================================================================
# 4. WishGenerator & Service Business Logic Tests
# ==============================================================================


@pytest.mark.anyio
async def test_wish_generator_with_mock_client():
    """Verify WishGenerator extracts profile data and passes to client."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(
        return_value="Happy 30th Birthday, Charlie! Rock on!"
    )

    generator = WishGenerator(client=mock_client)
    profile = {
        "name": "Charlie",
        "relationship": "Best Friend",
        "interests": "Rock music",
        "notes": "Always there for me",
        "wish_style": "Funny & Humorous",
    }

    wish, style = await generator.generate_birthday_wish(profile)

    assert wish == "Happy 30th Birthday, Charlie! Rock on!"
    assert style == "Funny"
    mock_client.generate_text.assert_awaited_once()


@pytest.mark.anyio
async def test_wish_generator_style_override():
    """Verify explicit style parameter overrides friend's profile style."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(return_value="A formal poetic greeting.")

    generator = WishGenerator(client=mock_client)
    profile = {
        "name": "Prof. Smith",
        "relationship": "Lecturer",
        "wish_style": "Formal & Polite",
    }

    wish, style = await generator.generate_birthday_wish(
        profile, style_override="Poetic"
    )

    assert style == "Poetic"
    args, kwargs = mock_client.generate_text.call_args
    assert "Poetic" in kwargs["prompt"]


@pytest.mark.anyio
async def test_generate_wish_for_friend_success(thread_safe_engine, sample_user_and_friend):
    """Verify service end-to-end wish generation with database profile."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(
        return_value="Dear Charlie, happy birthday to the truest best friend!"
    )

    result = await generate_wish_for_friend(
        telegram_id=sample_user_and_friend["telegram_id"],
        friend_id=sample_user_and_friend["friend_id"],
        engine=thread_safe_engine,
        ai_client=mock_client,
    )

    assert result["wish"] == "Dear Charlie, happy birthday to the truest best friend!"
    assert result["style"] == "Warm and heartfelt"
    assert result["friend"]["name"] == "Charlie"
    assert result["friend"]["relationship"] == "Best Friend"


@pytest.mark.anyio
async def test_generate_wish_for_friend_sparse_profile(thread_safe_engine):
    """Verify generation succeeds when friend has no relationship, notes, or interests."""
    from app.database.database import get_session

    with get_session(thread_safe_engine) as session:
        user = register_or_update_user(
            session=session, telegram_id=999000, first_name="Dave"
        )
        friend = add_friend_for_user(
            session=session,
            telegram_id=999000,
            name="SparseFriend",
            birth_month=4,
            birth_day=12,
        )
        sparse_friend_id = friend.id

    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(
        return_value="Wishing you a wonderful birthday, SparseFriend!"
    )

    result = await generate_wish_for_friend(
        telegram_id=999000,
        friend_id=sparse_friend_id,
        engine=thread_safe_engine,
        ai_client=mock_client,
    )

    assert "SparseFriend" in result["wish"]
    assert result["style"] == "Friendly"


@pytest.mark.anyio
async def test_generate_wish_for_friend_unauthorized(
    thread_safe_engine, sample_user_and_friend
):
    """Verify user A cannot generate a wish for user B's friend (user isolation)."""
    from app.database.database import get_session

    # Create User B (intruder)
    with get_session(thread_safe_engine) as session:
        register_or_update_user(
            session=session,
            telegram_id=88888888,
            first_name="Intruder",
        )

    mock_client = MagicMock(spec=GeminiClient)

    # Intruder tries to generate wish for Alice's friend Charlie
    with pytest.raises(FriendNotFoundError):
        await generate_wish_for_friend(
            telegram_id=88888888,
            friend_id=sample_user_and_friend["friend_id"],
            engine=thread_safe_engine,
            ai_client=mock_client,
        )


@pytest.mark.anyio
async def test_generate_wish_for_friend_unregistered_user(thread_safe_engine):
    """Verify unregistered user raises UserNotRegisteredError."""
    mock_client = MagicMock(spec=GeminiClient)
    with pytest.raises(UserNotRegisteredError):
        await generate_wish_for_friend(
            telegram_id=99999999999,
            friend_id=1,
            engine=thread_safe_engine,
            ai_client=mock_client,
        )


# ==============================================================================
# 5. Telegram Handlers & UI Flow Tests
# ==============================================================================


@pytest.mark.anyio
async def test_create_wish_command_unregistered_user(thread_safe_engine):
    """Verify /wish sends unregistered error if user is not in database."""
    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_msg
    mock_update.effective_user.id = 77777777

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await create_wish_command(mock_update, mock_context)

    mock_msg.reply_text.assert_awaited_once()
    assert mock_msg.reply_text.call_args[1]["text"] == UNREGISTERED_WISH_MESSAGE


@pytest.mark.anyio
async def test_create_wish_command_no_friends(thread_safe_engine):
    """Verify /wish displays empty friends prompt when user has 0 friends."""
    from app.database.database import get_session

    with get_session(thread_safe_engine) as session:
        register_or_update_user(session, telegram_id=111222, first_name="Friendless")

    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_msg
    mock_update.effective_user.id = 111222

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await create_wish_command(mock_update, mock_context)

    mock_msg.reply_text.assert_awaited_once()
    assert mock_msg.reply_text.call_args[1]["text"] == EMPTY_FRIENDS_WISH_MESSAGE


@pytest.mark.anyio
async def test_create_wish_command_presents_friend_buttons(
    thread_safe_engine, sample_user_and_friend
):
    """Verify /wish shows interactive friend selection buttons."""
    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_msg
    mock_update.effective_user.id = sample_user_and_friend["telegram_id"]

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await create_wish_command(mock_update, mock_context)

    mock_msg.reply_text.assert_awaited_once()
    call_args = mock_msg.reply_text.call_args
    assert "Select a friend" in call_args[1]["text"]
    reply_markup = call_args[1]["reply_markup"]
    assert isinstance(reply_markup, InlineKeyboardMarkup)
    # Charlie button should be present
    button_texts = [btn.text for row in reply_markup.inline_keyboard for btn in row]
    assert "👤 Charlie" in button_texts


@pytest.mark.anyio
async def test_wish_callback_cancel(thread_safe_engine):
    """Verify wish_cancel callback sends back to main menu."""
    mock_query = MagicMock()
    mock_query.data = "wish_cancel"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()
    mock_query.message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = 123456789

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await wish_callback_handler(mock_update, mock_context)

    mock_query.answer.assert_awaited_once()
    mock_query.edit_message_text.assert_awaited_once()
    assert "Returning to main menu" in mock_query.edit_message_text.call_args[1]["text"]
    mock_query.message.reply_text.assert_awaited_once()


@pytest.mark.anyio
async def test_wish_callback_select_friend_generates_wish(
    thread_safe_engine, sample_user_and_friend
):
    """Verify selecting a friend triggers AI generation and displays the wish."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(
        return_value="Happy Birthday Charlie! Keep snapping awesome photos!"
    )

    mock_query = MagicMock()
    mock_query.data = f"wish_select:{sample_user_and_friend['friend_id']}"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = sample_user_and_friend["telegram_id"]

    mock_context = MagicMock()
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "ai_client": mock_client,
    }

    await wish_callback_handler(mock_update, mock_context)

    # Should have edited message twice: 1. Loading, 2. Final wish result
    assert mock_query.edit_message_text.await_count == 2
    final_call = mock_query.edit_message_text.call_args_list[-1]
    final_text = final_call[1]["text"]
    assert "🎂 Birthday Wish for Charlie" in final_text
    assert "Keep snapping awesome photos!" in final_text
    assert "✨ Style: Warm and heartfelt" in final_text

    markup = final_call[1]["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    button_callbacks = [
        btn.callback_data for row in markup.inline_keyboard for btn in row
    ]
    assert any("wish_regen:" in cb for cb in button_callbacks)
    assert any("wish_styles:" in cb for cb in button_callbacks)
    assert "wish_cancel" in button_callbacks


@pytest.mark.anyio
async def test_wish_callback_unauthorized_friend(
    thread_safe_engine, sample_user_and_friend
):
    """Verify forged callback for someone else's friend displays access denied."""
    from app.database.database import get_session

    with get_session(thread_safe_engine) as session:
        register_or_update_user(session, telegram_id=555555, first_name="Stranger")

    mock_query = MagicMock()
    mock_query.data = f"wish_select:{sample_user_and_friend['friend_id']}"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = 555555  # Stranger

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await wish_callback_handler(mock_update, mock_context)

    mock_query.edit_message_text.assert_awaited_once()
    assert (
        mock_query.edit_message_text.call_args[1]["text"] == ACCESS_DENIED_MESSAGE
    )


@pytest.mark.anyio
async def test_wish_callback_regen(thread_safe_engine, sample_user_and_friend):
    """Verify wish_regen generates another variation."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(
        return_value="Another awesome wish for Charlie!"
    )

    mock_query = MagicMock()
    mock_query.data = f"wish_regen:{sample_user_and_friend['friend_id']}:Warm and heartfelt"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = sample_user_and_friend["telegram_id"]

    mock_context = MagicMock()
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "ai_client": mock_client,
    }

    await wish_callback_handler(mock_update, mock_context)

    assert mock_query.edit_message_text.await_count == 2
    final_text = mock_query.edit_message_text.call_args_list[-1][1]["text"]
    assert "Another awesome wish for Charlie!" in final_text


@pytest.mark.anyio
async def test_wish_callback_styles_menu_and_set_style(
    thread_safe_engine, sample_user_and_friend
):
    """Verify opening styles menu and selecting a specific style preset."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(
        return_value="A hilarious joke wish for Charlie!"
    )

    mock_query = MagicMock()
    mock_query.data = f"wish_styles:{sample_user_and_friend['friend_id']}"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = sample_user_and_friend["telegram_id"]

    mock_context = MagicMock()
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "ai_client": mock_client,
    }

    # 1. Open styles menu
    await wish_callback_handler(mock_update, mock_context)
    mock_query.edit_message_text.assert_awaited_once()
    assert "Select a wish style for Charlie" in mock_query.edit_message_text.call_args[1]["text"]

    # 2. Tap 'Funny' style
    mock_query.reset_mock()
    mock_query.data = f"wish_setstyle:{sample_user_and_friend['friend_id']}:Funny"

    await wish_callback_handler(mock_update, mock_context)
    assert mock_query.edit_message_text.await_count == 2
    final_text = mock_query.edit_message_text.call_args_list[-1][1]["text"]
    assert "A hilarious joke wish for Charlie!" in final_text
    assert "✨ Style: Funny" in final_text


@pytest.mark.anyio
async def test_wish_callback_missing_api_key_error(
    thread_safe_engine, sample_user_and_friend
):
    """Verify missing API key produces clear configuration notice in Telegram."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(
        side_effect=MissingApiKeyError("Missing AI_API_KEY")
    )

    mock_query = MagicMock()
    mock_query.data = f"wish_select:{sample_user_and_friend['friend_id']}"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = sample_user_and_friend["telegram_id"]

    mock_context = MagicMock()
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "ai_client": mock_client,
    }

    await wish_callback_handler(mock_update, mock_context)

    final_text = mock_query.edit_message_text.call_args_list[-1][1]["text"]
    assert final_text == MISSING_API_KEY_MESSAGE


@pytest.mark.anyio
async def test_wish_callback_rate_limit_error(
    thread_safe_engine, sample_user_and_friend
):
    """Verify rate limit error shows retry button in Telegram."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(
        side_effect=AIRateLimitError("Rate limit exceeded")
    )

    mock_query = MagicMock()
    mock_query.data = f"wish_select:{sample_user_and_friend['friend_id']}"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = sample_user_and_friend["telegram_id"]

    mock_context = MagicMock()
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "ai_client": mock_client,
    }

    await wish_callback_handler(mock_update, mock_context)

    final_call = mock_query.edit_message_text.call_args_list[-1]
    assert final_call[1]["text"] == RATE_LIMIT_MESSAGE
    reply_markup = final_call[1]["reply_markup"]
    btn_texts = [btn.text for row in reply_markup.inline_keyboard for btn in row]
    assert "🔄 Try Again" in btn_texts


@pytest.mark.anyio
async def test_wish_callback_timeout_error(
    thread_safe_engine, sample_user_and_friend
):
    """Verify timeout error displays helpful prompt and retry button."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(side_effect=AITimeoutError("Timed out"))

    mock_query = MagicMock()
    mock_query.data = f"wish_select:{sample_user_and_friend['friend_id']}"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = sample_user_and_friend["telegram_id"]

    mock_context = MagicMock()
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "ai_client": mock_client,
    }

    await wish_callback_handler(mock_update, mock_context)

    final_call = mock_query.edit_message_text.call_args_list[-1]
    assert final_call[1]["text"] == TIMEOUT_MESSAGE


@pytest.mark.anyio
async def test_wish_callback_generic_ai_error(
    thread_safe_engine, sample_user_and_friend
):
    """Verify generic AI response error displays graceful failure message."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(
        side_effect=AIResponseError("Safety filtered")
    )

    mock_query = MagicMock()
    mock_query.data = f"wish_select:{sample_user_and_friend['friend_id']}"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = sample_user_and_friend["telegram_id"]

    mock_context = MagicMock()
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "ai_client": mock_client,
    }

    await wish_callback_handler(mock_update, mock_context)

    final_call = mock_query.edit_message_text.call_args_list[-1]
    assert final_call[1]["text"] == GENERIC_AI_ERROR_MESSAGE


@pytest.mark.anyio
async def test_wish_callback_high_demand_503_error(
    thread_safe_engine, sample_user_and_friend
):
    """Verify 503 high demand displays HIGH_DEMAND_MESSAGE and retry button."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(
        side_effect=AIServiceUnavailableError("503 high demand")
    )

    mock_query = MagicMock()
    mock_query.data = f"wish_select:{sample_user_and_friend['friend_id']}"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = sample_user_and_friend["telegram_id"]

    mock_context = MagicMock()
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "ai_client": mock_client,
    }

    await wish_callback_handler(mock_update, mock_context)

    final_call = mock_query.edit_message_text.call_args_list[-1]
    assert final_call[1]["text"] == HIGH_DEMAND_MESSAGE
    reply_markup = final_call[1]["reply_markup"]
    btn_texts = [btn.text for row in reply_markup.inline_keyboard for btn in row]
    assert "🔄 Try Again" in btn_texts


@pytest.mark.anyio
async def test_wish_callback_quota_exhausted_error(
    thread_safe_engine, sample_user_and_friend
):
    """Verify quota exhausted displays QUOTA_EXHAUSTED_MESSAGE."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(
        side_effect=AIQuotaExhaustedError("Quota exhausted")
    )

    mock_query = MagicMock()
    mock_query.data = f"wish_select:{sample_user_and_friend['friend_id']}"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query
    mock_update.effective_user.id = sample_user_and_friend["telegram_id"]

    mock_context = MagicMock()
    mock_context.bot_data = {
        "db_engine": thread_safe_engine,
        "ai_client": mock_client,
    }

    await wish_callback_handler(mock_update, mock_context)

    final_call = mock_query.edit_message_text.call_args_list[-1]
    assert final_call[1]["text"] == QUOTA_EXHAUSTED_MESSAGE


# ==============================================================================
# 6. Keyboards Structure Tests
# ==============================================================================


def test_wish_keyboards():
    """Verify wish keyboard generation and button payloads."""
    # Friend selection keyboard
    friends = [
        {"id": 1, "name": "Luna"},
        {"id": 2, "name": "Nox"},
    ]
    markup = get_wish_friend_selection_keyboard(friends)
    assert isinstance(markup, InlineKeyboardMarkup)
    assert markup.inline_keyboard[0][0].text == "👤 Luna"
    assert markup.inline_keyboard[0][0].callback_data == "wish_select:1"
    assert markup.inline_keyboard[0][1].text == "👤 Nox"
    assert markup.inline_keyboard[0][1].callback_data == "wish_select:2"
    assert markup.inline_keyboard[1][0].text == "🔙 Main Menu"

    # Result keyboard
    res_markup = get_wish_result_keyboard(1, "Funny")
    assert res_markup.inline_keyboard[0][0].callback_data == "wish_regen:1:Funny"
    assert res_markup.inline_keyboard[1][0].callback_data == "wish_styles:1"
    assert res_markup.inline_keyboard[2][0].callback_data == "wish_cancel"

    # Styles keyboard
    styles_markup = get_wish_styles_keyboard(1)
    all_styles_buttons = [
        btn.text for row in styles_markup.inline_keyboard for btn in row
    ]
    for s in SUPPORTED_STYLES:
        assert s in all_styles_buttons

    # Error keyboard
    err_markup = get_wish_error_keyboard(1)
    assert err_markup.inline_keyboard[0][0].text == "🔄 Try Again"
    assert err_markup.inline_keyboard[0][0].callback_data == "wish_select:1"

    err_no_friend = get_wish_error_keyboard(None)
    assert len(err_no_friend.inline_keyboard) == 1
    assert err_no_friend.inline_keyboard[0][0].callback_data == "wish_cancel"
