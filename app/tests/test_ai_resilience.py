"""
Focused unit tests for Gemini AI Birthday Wish Generation Reliability.
Validates all 18 resilience and fallback scenarios using mocked HTTP responses:
1. Primary model successfully generates a wish.
2. Primary model returns HTTP 503 and succeeds on retry.
3. Primary model returns HTTP 503 repeatedly and fallback succeeds.
4. Primary and fallback both return HTTP 503.
5. Temporary HTTP 429 rate limiting.
6. HTTP 429 quota exhaustion.
7. HTTP 401 invalid API key.
8. HTTP 404 model unavailable.
9. HTTP 400 malformed request.
10. Connection timeout.
11. Network connection failure.
12. Empty Gemini response.
13. Safety-filter response.
14. Successful wish regeneration.
15. Failed regeneration preserves existing wish content.
16. Zero retry configuration.
17. Missing fallback configuration.
18. Primary and fallback model names are identical.
Plus connection pooling, clean shutdown, and multiple text parts.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from app.ai.client import (
    AIError,
    AIModelUnavailableError,
    AINetworkError,
    AIQuotaExhaustedError,
    AIRateLimitError,
    AIResponseError,
    AIServiceUnavailableError,
    AITimeoutError,
    GeminiClient,
    MissingApiKeyError,
)
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from app.database.database import get_session, init_db

@pytest.fixture
def thread_safe_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    yield engine
    engine.dispose()

from app.database.models.friend import Friend
from app.database.models.user import User
from app.database.models.wish import Wish, WishStatus
from app.database.repositories.wish_repo import WishRepository
from app.services.wish_ser import create_or_regenerate_wish


def _build_mock_gemini_response(
    text: str = "Wishing you a happy birthday!",
    status_code: int = 200,
    parts: list = None,
    finish_reason: str = "STOP",
) -> httpx.Response:
    """Build a mock httpx.Response simulating Gemini REST generateContent API."""
    if status_code != 200:
        return httpx.Response(
            status_code=status_code,
            json={"error": {"code": status_code, "message": text}},
            request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent"),
        )

    parts_payload = parts if parts is not None else [{"text": text}]
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": parts_payload,
                    "role": "model",
                },
                "finishReason": finish_reason,
            }
        ]
    }
    return httpx.Response(
        status_code=200,
        json=payload,
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent"),
    )


# ==============================================================================
# Scenario 1: Primary model successfully generates a wish
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_1_primary_model_successfully_generates_wish():
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
    )
    mock_resp = _build_mock_gemini_response("Happy 30th Birthday, Alex!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        result = await client.generate_text("Prompt for Alex")

        assert result == "Happy 30th Birthday, Alex!"
        assert client.last_used_model == "gemini-3.8-flash"
        assert mock_post.await_count == 1
        called_url = str(mock_post.call_args[0][0])
        assert "models/gemini-3.8-flash:generateContent" in called_url


# ==============================================================================
# Scenario 2: Primary model returns HTTP 503 and succeeds on retry
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_2_primary_model_503_succeeds_on_retry():
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
        max_retries=2,
        base_backoff=0.01,
        sleep_func=mock_sleep,
    )
    resp_503 = _build_mock_gemini_response("High demand", status_code=503)
    resp_200 = _build_mock_gemini_response("Recovered wish on attempt 2!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_503, resp_200]

        result = await client.generate_text("Prompt")

        assert result == "Recovered wish on attempt 2!"
        assert client.last_used_model == "gemini-3.8-flash"
        assert mock_post.await_count == 2
        mock_sleep.assert_awaited_once()


# ==============================================================================
# Scenario 3: Primary returns HTTP 503 repeatedly and fallback succeeds
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_3_primary_503_repeatedly_and_fallback_succeeds():
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
        max_retries=1,  # 1 initial + 1 retry = 2 attempts on primary
        base_backoff=0.01,
        sleep_func=mock_sleep,
    )
    resp_503 = _build_mock_gemini_response("High demand", status_code=503)
    resp_200 = _build_mock_gemini_response("Fallback wish from gemini-3.5-flash-lite!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_503, resp_503, resp_200]

        result = await client.generate_text("Prompt")

        assert result == "Fallback wish from gemini-3.5-flash-lite!"
        assert client.last_used_model == "gemini-3.5-flash-lite"
        assert mock_post.await_count == 3
        # First 2 requests to primary, 3rd to fallback
        assert "models/gemini-3.8-flash" in str(mock_post.call_args_list[0][0][0])
        assert "models/gemini-3.8-flash" in str(mock_post.call_args_list[1][0][0])
        assert "models/gemini-3.5-flash-lite" in str(mock_post.call_args_list[2][0][0])


# ==============================================================================
# Scenario 4: Primary and fallback both return HTTP 503
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_4_primary_and_fallback_both_return_503():
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
        max_retries=1,
        base_backoff=0.01,
        sleep_func=mock_sleep,
    )
    resp_503 = _build_mock_gemini_response("High demand", status_code=503)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # 2 calls on primary, 2 calls on fallback
        mock_post.side_effect = [resp_503, resp_503, resp_503, resp_503]

        with pytest.raises(AIServiceUnavailableError) as exc_info:
            await client.generate_text("Prompt")

        assert "503" in str(exc_info.value)
        assert mock_post.await_count == 4


# ==============================================================================
# Scenario 5: Temporary HTTP 429 rate limiting
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_5_temporary_http_429_rate_limiting():
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
        max_retries=2,
        base_backoff=0.01,
        sleep_func=mock_sleep,
    )
    # Burst 429 without quota keywords
    resp_429 = _build_mock_gemini_response("Too many requests. Please wait.", status_code=429)
    resp_200 = _build_mock_gemini_response("Success after burst delay!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_429, resp_200]

        result = await client.generate_text("Prompt")

        assert result == "Success after burst delay!"
        assert client.last_used_model == "gemini-3.8-flash"
        assert mock_post.await_count == 2
        mock_sleep.assert_awaited_once()


# ==============================================================================
# Scenario 6: HTTP 429 quota exhaustion
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_6_http_429_quota_exhaustion_triggers_fallback():
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
        max_retries=2,
        sleep_func=mock_sleep,
    )
    resp_429_quota = _build_mock_gemini_response(
        "Resource has been exhausted (check daily quota limit).", status_code=429
    )
    resp_200 = _build_mock_gemini_response("Wish from fallback model with remaining quota!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_429_quota, resp_200]

        result = await client.generate_text("Prompt")

        assert result == "Wish from fallback model with remaining quota!"
        assert client.last_used_model == "gemini-3.5-flash-lite"
        # Fails fast on primary with 1 call, then succeeds on fallback with 1 call = 2 calls total
        assert mock_post.await_count == 2
        mock_sleep.assert_not_called()


# ==============================================================================
# Scenario 7: HTTP 401 invalid API key
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_7_http_401_invalid_api_key():
    client = GeminiClient(
        api_key="invalid-api-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
    )
    resp_401 = _build_mock_gemini_response("API key not valid", status_code=401)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = resp_401

        with pytest.raises(MissingApiKeyError) as exc_info:
            await client.generate_text("Prompt")

        assert "401" in str(exc_info.value)
        # Fails immediately without retries and without attempting fallback
        assert mock_post.await_count == 1


# ==============================================================================
# Scenario 8: HTTP 404 model unavailable
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_8_http_404_model_unavailable_triggers_fallback():
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-deprecated-model",
        fallback_model="gemini-3.5-flash-lite",
    )
    resp_404 = _build_mock_gemini_response("Model not found", status_code=404)
    resp_200 = _build_mock_gemini_response("Wish from available fallback model!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_404, resp_200]

        result = await client.generate_text("Prompt")

        assert result == "Wish from available fallback model!"
        assert client.last_used_model == "gemini-3.5-flash-lite"
        assert mock_post.await_count == 2


# ==============================================================================
# Scenario 9: HTTP 400 malformed request
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_9_http_400_malformed_request():
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
    )
    resp_400 = _build_mock_gemini_response("Invalid JSON or bad arguments", status_code=400)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = resp_400

        with pytest.raises(AIResponseError) as exc_info:
            await client.generate_text("Prompt")

        assert "HTTP 400" in str(exc_info.value)
        # 400 does NOT retry and does NOT trigger fallback
        assert mock_post.await_count == 1


# ==============================================================================
# Scenario 10: Connection timeout
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_10_connection_timeout_triggers_fallback():
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
        max_retries=1,
        base_backoff=0.01,
        sleep_func=mock_sleep,
    )
    req = httpx.Request("POST", "https://example.com")
    timeout_err = httpx.ConnectTimeout("Connect timeout", request=req)
    resp_200 = _build_mock_gemini_response("Wish from fallback after connect timeout!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # Primary times out twice, then fallback succeeds
        mock_post.side_effect = [timeout_err, timeout_err, resp_200]

        result = await client.generate_text("Prompt")

        assert result == "Wish from fallback after connect timeout!"
        assert client.last_used_model == "gemini-3.5-flash-lite"
        assert mock_post.await_count == 3


# ==============================================================================
# Scenario 11: Network connection failure
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_11_network_connection_failure_triggers_fallback():
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
        max_retries=1,
        base_backoff=0.01,
        sleep_func=mock_sleep,
    )
    req = httpx.Request("POST", "https://example.com")
    net_err = httpx.ConnectError("Network unreachable", request=req)
    resp_200 = _build_mock_gemini_response("Wish from fallback after network failure!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [net_err, net_err, resp_200]

        result = await client.generate_text("Prompt")

        assert result == "Wish from fallback after network failure!"
        assert client.last_used_model == "gemini-3.5-flash-lite"
        assert mock_post.await_count == 3


# ==============================================================================
# Scenario 12: Empty Gemini response
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_12_empty_gemini_response():
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model=None,
    )
    empty_candidates_resp = httpx.Response(
        status_code=200,
        json={"candidates": []},
        request=httpx.Request("POST", "https://example.com"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = empty_candidates_resp

        with pytest.raises(AIResponseError) as exc_info:
            await client.generate_text("Prompt")

        assert "candidates" in str(exc_info.value).lower()


# ==============================================================================
# Scenario 13: Safety-filter response
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_13_safety_filter_response():
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
    )
    safety_resp = httpx.Response(
        status_code=200,
        json={"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]},
        request=httpx.Request("POST", "https://example.com"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = safety_resp

        with pytest.raises(AIResponseError) as exc_info:
            await client.generate_text("Prompt")

        assert "safety filter" in str(exc_info.value).lower()
        # Safety blocks do NOT attempt fallback
        assert mock_post.await_count == 1


# ==============================================================================
# Scenario 14: Successful wish regeneration
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_14_successful_wish_regeneration(thread_safe_engine):
    with get_session(thread_safe_engine) as session:
        u = User(telegram_id=987654, username="testuser", first_name="Test")
        session.add(u)
        session.flush()
        f = Friend(user_id=u.id, name="Emily", birth_month=10, birth_day=15, birth_year=1995)
        session.add(f)
        session.flush()
        w = Wish(
            user_id=u.id,
            friend_id=f.id,
            content="Original wish for Emily",
            style="Friendly",
            status=WishStatus.DRAFT,
        )
        session.add(w)
        session.commit()
        w_id = w.id
        f_id = f.id

    mock_client = MagicMock(spec=GeminiClient)
    mock_client.generate_text = AsyncMock(return_value="Regenerated brand new wish for Emily!")

    result = await create_or_regenerate_wish(
        telegram_id=987654,
        friend_id=f_id,
        existing_wish_id=w_id,
        style="Humorous",
        engine=thread_safe_engine,
        ai_client=mock_client,
    )

    assert result["wish"] == "Regenerated brand new wish for Emily!"
    assert result["style"] == "Humorous"

    # Verify database was updated and kept in DRAFT status
    with get_session(thread_safe_engine) as session:
        repo = WishRepository(session)
        updated_w = repo.get_by_id(w_id, u.id)
        assert updated_w.content == "Regenerated brand new wish for Emily!"
        assert updated_w.style == "Humorous"
        assert updated_w.status == WishStatus.DRAFT


# ==============================================================================
# Scenario 15: Failed regeneration preserves existing wish content
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_15_failed_regeneration_preserves_existing_wish(thread_safe_engine):
    with get_session(thread_safe_engine) as session:
        u = User(telegram_id=999888, username="testpreservation", first_name="Preserve")
        session.add(u)
        session.flush()
        f = Friend(user_id=u.id, name="Sam", birth_month=5, birth_day=20, birth_year=1990)
        session.add(f)
        session.flush()
        w = Wish(
            user_id=u.id,
            friend_id=f.id,
            content="Original untouched wish text",
            style="Warm",
            status=WishStatus.DRAFT,
        )
        session.add(w)
        session.commit()
        w_id = w.id
        f_id = f.id

    mock_client = MagicMock(spec=GeminiClient)
    # AI fails with HTTP 503 high demand
    mock_client.generate_text = AsyncMock(
        side_effect=AIServiceUnavailableError("Model 503 high demand")
    )

    with pytest.raises(AIServiceUnavailableError):
        await create_or_regenerate_wish(
            telegram_id=999888,
            friend_id=f_id,
            existing_wish_id=w_id,
            style="Poetic",
            engine=thread_safe_engine,
            ai_client=mock_client,
        )

    # Verify database was NOT touched or corrupted
    with get_session(thread_safe_engine) as session:
        repo = WishRepository(session)
        preserved_w = repo.get_by_id(w_id, u.id)
        assert preserved_w.content == "Original untouched wish text"
        assert preserved_w.style == "Warm"
        assert preserved_w.status == WishStatus.DRAFT


# ==============================================================================
# Scenario 16: Zero retry configuration
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_16_zero_retry_configuration():
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
        max_retries=0,  # Exactly 1 attempt on primary, 1 attempt on fallback
        sleep_func=mock_sleep,
    )
    resp_503 = _build_mock_gemini_response("High demand", status_code=503)
    resp_200 = _build_mock_gemini_response("Wish from fallback with 0 retries!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_503, resp_200]

        result = await client.generate_text("Prompt")

        assert result == "Wish from fallback with 0 retries!"
        assert client.last_used_model == "gemini-3.5-flash-lite"
        # Exactly 1 attempt on primary, 1 on fallback = 2 attempts total
        assert mock_post.await_count == 2
        mock_sleep.assert_not_called()


# ==============================================================================
# Scenario 17: Missing fallback configuration
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_17_missing_fallback_configuration():
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model=None,
        max_retries=0,
    )
    assert client.fallback_model is None
    resp_503 = _build_mock_gemini_response("High demand", status_code=503)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = resp_503

        with pytest.raises(AIServiceUnavailableError):
            await client.generate_text("Prompt")

        assert mock_post.await_count == 1


# ==============================================================================
# Scenario 18: Primary and fallback model names are identical
# ==============================================================================


def test_scenario_18_identical_models_disables_fallback():
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.8-flash",
    )
    assert client.model == "gemini-3.8-flash"
    assert client.fallback_model is None


# ==============================================================================
# Additional Tests: Multiple Text Parts, Connection Pooling & Clean Shutdown
# ==============================================================================


@pytest.mark.anyio
async def test_gemini_response_extraction_multiple_text_parts():
    """Verify multiple text parts in candidate are merged cleanly without thought parts."""
    client = GeminiClient(api_key="valid-test-key", fallback_model=None)
    mock_resp = _build_mock_gemini_response(
        parts=[
            {"thought": True, "text": "Thinking about a cat-themed wish..."},
            {"text": "Happy Birthday! "},
            {"text": "Hope your day is purr-fect!"},
        ]
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        result = await client.generate_text("Prompt")

        assert result == "Happy Birthday! Hope your day is purr-fect!"


@pytest.mark.anyio
async def test_gemini_client_connection_pooling_and_clean_close():
    """Verify GeminiClient reuses httpx.AsyncClient instance and closes it on shutdown."""
    client = GeminiClient(api_key="valid-test-key")
    http_c1 = await client.get_http_client()
    http_c2 = await client.get_http_client()

    assert http_c1 is http_c2
    assert not http_c1.is_closed

    await client.close()
    assert http_c1.is_closed
    assert client._http_client is None
