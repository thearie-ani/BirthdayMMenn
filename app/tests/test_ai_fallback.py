"""
Automated tests for Gemini Model Configuration & Reliable Model Fallback (Stage 12 Step 4).
Validates:
1. Configured primary model and optional fallback model.
2. Successful primary request.
3. Eligible primary failure followed by fallback success (404, 503, 429, timeout).
4. Both models failing raises proper terminal exception.
5. Quota exhaustion without uncontrolled retries (1 attempt per model, no retry loops).
6. Ineligible errors (401 invalid key, 400 bad request, safety filter) fail fast without fallback.
7. Fallback disabled when no fallback model is configured.
8. WishGenerator integration with fallback support.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from app.ai.client import (
    AIError,
    AIModelUnavailableError,
    AIQuotaExhaustedError,
    AIRateLimitError,
    AIResponseError,
    AIServiceUnavailableError,
    AITimeoutError,
    GeminiClient,
    MissingApiKeyError,
)
from app.ai.wish_generator import WishGenerator


def _build_success_response(text: str = "Happy Birthday! Wishing you a fantastic year ahead!") -> httpx.Response:
    """Build a mock httpx.Response returning Gemini generateContent success JSON."""
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ]
    }
    return httpx.Response(
        status_code=200,
        json=payload,
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent"),
    )


def _build_error_response(status_code: int, error_message: str = "Error details") -> httpx.Response:
    """Build a mock httpx.Response with specified HTTP error status code."""
    payload = {"error": {"code": status_code, "message": error_message}}
    return httpx.Response(
        status_code=status_code,
        json=payload,
        request=httpx.Request("POST", f"https://generativelanguage.googleapis.com/v1beta/models/test:generateContent"),
    )


# ==============================================================================
# 1. Model Configuration Tests
# ==============================================================================


def test_gemini_client_configuration_defaults():
    """Verify GeminiClient picks up configured primary model and fallback model."""
    with patch.dict("os.environ", {"GEMINI_MODEL": "gemini-3.8-flash", "GEMINI_FALLBACK_MODEL": "gemini-3-flash-preview"}):
        client = GeminiClient(api_key="valid-test-key")
        assert client.model == "gemini-3.8-flash"
        assert client.fallback_model == "gemini-3-flash-preview"


def test_gemini_client_explicit_models_override():
    """Verify explicitly passed models override environment defaults."""
    client = GeminiClient(
        api_key="valid-test-key",
        model="custom-primary-model",
        fallback_model="custom-fallback-model",
    )
    assert client.model == "custom-primary-model"
    assert client.fallback_model == "custom-fallback-model"


def test_gemini_client_identical_models_disables_fallback():
    """Verify fallback is disabled if fallback_model matches primary model."""
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3.8-flash",
    )
    assert client.model == "gemini-3.8-flash"
    assert client.fallback_model is None


def test_gemini_client_no_fallback_configured():
    """Verify client has None fallback_model when none is configured in env or args."""
    with patch.dict("os.environ", {}, clear=True):
        client = GeminiClient(api_key="valid-test-key", model="gemini-3.8-flash", fallback_model=None)
        assert client.fallback_model is None


# ==============================================================================
# 2. Successful Primary Request Tests
# ==============================================================================


@pytest.mark.anyio
async def test_successful_primary_request_no_fallback_needed():
    """Verify primary model succeeds on first attempt and fallback is never invoked."""
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3-flash-preview",
    )

    mock_resp = _build_success_response("🎉 Happy Birthday from Primary Model!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        result = await client.generate_text("Generate a birthday wish")

        assert result == "🎉 Happy Birthday from Primary Model!"
        assert client.last_used_model == "gemini-3.8-flash"
        assert mock_post.await_count == 1
        called_url = str(mock_post.call_args[0][0])
        assert "models/gemini-3.8-flash:generateContent" in called_url


# ==============================================================================
# 3. Eligible Primary Failure -> Fallback Success Tests
# ==============================================================================


@pytest.mark.anyio
async def test_fallback_on_primary_model_not_found_404():
    """Verify that if primary model returns 404 (unavailable/deprecated), fallback succeeds."""
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-old-model",
        fallback_model="gemini-3-flash-preview",
    )

    resp_404 = _build_error_response(404, "models/gemini-old-model is not found or unavailable")
    resp_success = _build_success_response("🎉 Happy Birthday from Fallback Model!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_404, resp_success]

        result = await client.generate_text("Generate a birthday wish")

        assert result == "🎉 Happy Birthday from Fallback Model!"
        assert client.last_used_model == "gemini-3-flash-preview"
        assert mock_post.await_count == 2
        first_url = str(mock_post.call_args_list[0][0][0])
        second_url = str(mock_post.call_args_list[1][0][0])
        assert "models/gemini-old-model" in first_url
        assert "models/gemini-3-flash-preview" in second_url


@pytest.mark.anyio
async def test_fallback_on_primary_service_unavailable_503():
    """Verify that if primary model encounters persistent 503 high demand, fallback succeeds."""
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3-flash-preview",
        max_retries=1,  # 1 initial + 1 retry = 2 attempts on primary
        sleep_func=mock_sleep,
    )

    resp_503 = _build_error_response(503, "Model experiencing high demand")
    resp_success = _build_success_response("🎉 Success from fallback after 503!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # Primary fails twice (initial + 1 retry), then fallback succeeds on first attempt
        mock_post.side_effect = [resp_503, resp_503, resp_success]

        result = await client.generate_text("Generate wish")

        assert result == "🎉 Success from fallback after 503!"
        assert client.last_used_model == "gemini-3-flash-preview"
        assert mock_post.await_count == 3


@pytest.mark.anyio
async def test_fallback_on_primary_timeout():
    """Verify that if primary model times out, fallback succeeds."""
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3-flash-preview",
        max_retries=1,
        sleep_func=mock_sleep,
    )

    req = httpx.Request("POST", "https://api.google.com")
    timeout_exc = httpx.ReadTimeout("Read timed out", request=req)
    resp_success = _build_success_response("🎉 Success from fallback after timeout!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [timeout_exc, timeout_exc, resp_success]

        result = await client.generate_text("Generate wish")

        assert result == "🎉 Success from fallback after timeout!"
        assert client.last_used_model == "gemini-3-flash-preview"


@pytest.mark.anyio
async def test_fallback_on_primary_quota_exhausted_zero_retry_loops():
    """
    Verify that if primary model returns 429 Quota Exhausted:
    - Primary is NOT retried in an endless loop (fails fast on primary).
    - Client immediately tries the fallback model.
    - Fallback succeeds.
    """
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3-flash-preview",
        max_retries=2,
        sleep_func=mock_sleep,
    )

    resp_429_quota = _build_error_response(429, "Resource has been exhausted (e.g. check quota limit).")
    resp_success = _build_success_response("🎉 Success from fallback model after primary quota exhausted!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_429_quota, resp_success]

        result = await client.generate_text("Generate wish")

        assert result == "🎉 Success from fallback model after primary quota exhausted!"
        assert client.last_used_model == "gemini-3-flash-preview"
        # Exactly 2 requests made: 1 for primary, 1 for fallback!
        assert mock_post.await_count == 2
        mock_sleep.assert_not_called()


# ==============================================================================
# 4. Terminal Failures (Both Models Fail or No Fallback)
# ==============================================================================


@pytest.mark.anyio
async def test_both_models_fail_raises_fallback_exception():
    """Verify that if both primary and fallback encounter 503, AIServiceUnavailableError is raised."""
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3-flash-preview",
        max_retries=1,
        sleep_func=mock_sleep,
    )

    resp_503 = _build_error_response(503, "High demand")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # Primary fails twice, Fallback fails twice
        mock_post.side_effect = [resp_503, resp_503, resp_503, resp_503]

        with pytest.raises(AIServiceUnavailableError):
            await client.generate_text("Generate wish")

        assert mock_post.await_count == 4


@pytest.mark.anyio
async def test_both_models_quota_exhausted_fails_fast_without_uncontrolled_retries():
    """
    Verify that if both models report quota exhaustion:
    - Primary fails on attempt 1 with zero retries.
    - Fallback fails on attempt 1 with zero retries.
    - Exactly 2 HTTP calls total.
    - Raises AIQuotaExhaustedError cleanly.
    """
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3-flash-preview",
        max_retries=2,
        sleep_func=mock_sleep,
    )

    resp_429_quota = _build_error_response(429, "Daily quota limit exceeded.")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_429_quota, resp_429_quota]

        with pytest.raises(AIQuotaExhaustedError):
            await client.generate_text("Generate wish")

        assert mock_post.await_count == 2
        mock_sleep.assert_not_called()


@pytest.mark.anyio
async def test_no_fallback_configured_raises_primary_error_directly():
    """Verify that when no fallback model is configured, primary error is raised directly."""
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model=None,
    )

    resp_404 = _build_error_response(404, "Model not found")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = resp_404

        with pytest.raises(AIModelUnavailableError):
            await client.generate_text("Generate wish")

        assert mock_post.await_count == 1


# ==============================================================================
# 5. Ineligible Errors Fail Fast Without Fallback
# ==============================================================================


@pytest.mark.anyio
async def test_ineligible_invalid_api_key_401_no_fallback():
    """Verify 401 Unauthorized raises MissingApiKeyError immediately without calling fallback."""
    client = GeminiClient(
        api_key="bad-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3-flash-preview",
    )

    resp_401 = _build_error_response(401, "API key not valid. Please pass a valid API key.")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = resp_401

        with pytest.raises(MissingApiKeyError):
            await client.generate_text("Generate wish")

        # Fallback was NOT attempted
        assert mock_post.await_count == 1


@pytest.mark.anyio
async def test_ineligible_bad_request_400_no_fallback():
    """Verify 400 Bad Request raises AIResponseError immediately without calling fallback."""
    client = GeminiClient(
        api_key="valid-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3-flash-preview",
    )

    resp_400 = _build_error_response(400, "Client error: Invalid argument in request payload.")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = resp_400

        with pytest.raises(AIResponseError):
            await client.generate_text("Generate wish")

        assert mock_post.await_count == 1


@pytest.mark.anyio
async def test_ineligible_safety_filter_blocked_no_fallback():
    """Verify safety block raises AIResponseError immediately without calling fallback."""
    client = GeminiClient(
        api_key="valid-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3-flash-preview",
    )

    blocked_resp = httpx.Response(
        status_code=200,
        json={"promptFeedback": {"blockReason": "SAFETY"}},
        request=httpx.Request("POST", "https://api.google.com"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = blocked_resp

        with pytest.raises(AIResponseError) as exc_info:
            await client.generate_text("Generate wish")

        assert "safety filter" in str(exc_info.value).lower()
        assert mock_post.await_count == 1


# ==============================================================================
# 6. WishGenerator Integration with Fallback
# ==============================================================================


@pytest.mark.anyio
async def test_wish_generator_integration_with_fallback():
    """Verify WishGenerator seamlessly utilizes client fallback on 503 high demand."""
    mock_sleep = AsyncMock()
    client = GeminiClient(
        api_key="valid-test-key",
        model="gemini-3.8-flash",
        fallback_model="gemini-3-flash-preview",
        max_retries=0,  # 1 attempt per model
        sleep_func=mock_sleep,
    )

    generator = WishGenerator(client=client)

    resp_503 = _build_error_response(503, "High demand")
    resp_success = _build_success_response("🎂 Wishing you an amazing birthday filled with joy!")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_503, resp_success]

        profile = {
            "name": "Alice",
            "relationship": "Colleague",
            "interests": "Coffee, Tech",
            "notes": "Always helpful",
            "wish_style": "Friendly",
        }

        wish_text, final_style = await generator.generate_birthday_wish(profile)

        assert wish_text == "🎂 Wishing you an amazing birthday filled with joy!"
        assert final_style == "Friendly"
        assert client.last_used_model == "gemini-3-flash-preview"
        assert mock_post.await_count == 2
