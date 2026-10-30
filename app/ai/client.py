"""
Async Gemini REST API client using httpx with granular timeouts and safe diagnostic logging.
"""
import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional
import httpx

from app.config.settings import get_ai_api_key, get_ai_model

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_CONNECT_TIMEOUT = 10.0   # Max seconds to establish connection with Google API
DEFAULT_READ_TIMEOUT = 35.0      # Max seconds to wait for model response to be generated
DEFAULT_WRITE_TIMEOUT = 10.0     # Max seconds to transmit request payload
DEFAULT_POOL_TIMEOUT = 5.0       # Max seconds to acquire connection from pool
DEFAULT_MAX_RETRIES = 2          # Maximum retries after initial attempt (3 attempts total)
DEFAULT_BASE_BACKOFF = 1.0       # Initial backoff delay in seconds
DEFAULT_MAX_BACKOFF = 6.0        # Maximum exponential backoff delay
DEFAULT_MAX_RETRY_AFTER_WAIT = 10.0  # Max acceptable delay from Retry-After header


class AIError(Exception):
    """Base exception for all AI operations."""
    pass


class MissingApiKeyError(AIError):
    """Raised when the AI API key is missing, empty, or set to placeholder."""
    pass


class AIRateLimitError(AIError):
    """Raised when the AI provider returns a rate limit (HTTP 429)."""
    pass


class AIQuotaExhaustedError(AIRateLimitError):
    """Raised when the AI provider quota is fully exhausted."""
    pass


class AIServiceUnavailableError(AIError):
    """Raised when the AI service is experiencing high demand or temporary 5xx error after retries."""
    pass


class AITimeoutError(AIError):
    """Raised when the network request to the AI provider times out."""
    pass


class AIResponseError(AIError):
    """Raised when the AI provider returns an error, invalid response, or empty text."""
    pass


class GeminiClient:
    """
    Asynchronous client for interacting with the Google Gemini REST API.
    Features granular connection/read timeouts, exponential backoff retries,
    and safe diagnostic logging.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        pool_timeout: float = DEFAULT_POOL_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_backoff: float = DEFAULT_BASE_BACKOFF,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
        max_retry_after_wait: float = DEFAULT_MAX_RETRY_AFTER_WAIT,
        sleep_func: Optional[Callable[[float], Any]] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else get_ai_api_key()
        self.model = (model or get_ai_model()).strip().strip("\"'")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout
        self.pool_timeout = pool_timeout
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.max_retry_after_wait = max_retry_after_wait
        self.sleep_func = sleep_func

    @property
    def timeout_config(self) -> httpx.Timeout:
        """Construct granular httpx.Timeout instance."""
        return httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.pool_timeout,
        )

    def _validate_configuration(self) -> None:
        """Verify that an API key is provided and not set to a placeholder."""
        if not self.api_key or self.api_key.strip() in {"", "your_ai_api_key_here"}:
            raise MissingApiKeyError(
                "AI_API_KEY is not configured or is still set to placeholder."
            )

    def _calculate_delay(self, response: Optional[httpx.Response], attempt: int) -> float:
        """
        Determine retry backoff delay, respecting Retry-After header if provided.
        """
        if response is not None:
            retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
            if retry_after:
                try:
                    sec = float(retry_after.strip())
                    if 0 < sec <= self.max_retry_after_wait:
                        return sec
                except ValueError:
                    pass

        # Exponential backoff: base * 2^attempt (e.g. 1.0s, 2.0s)
        backoff = self.base_backoff * (2 ** attempt)
        return min(backoff, self.max_backoff)

    async def _sleep(self, seconds: float) -> None:
        """Internal sleep helper; can be overridden or mocked in tests."""
        if self.sleep_func is not None:
            res = self.sleep_func(seconds)
            if hasattr(res, "__await__"):
                await res
        else:
            await asyncio.sleep(seconds)

    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        max_output_tokens: int = 600,
    ) -> str:
        """
        Generate text completion using Google Gemini REST generateContent API.
        Compatible with Gemini 3.8 and Flash models.
        Automatically retries temporary 503 / 5xx / 429 / connection timeout errors up to max_retries.

        Args:
            prompt: User prompt content.
            system_instruction: Optional system instruction for tone/behavior.
            max_output_tokens: Maximum tokens in response.

        Returns:
            The generated text response.

        Raises:
            MissingApiKeyError: If API key is missing, invalid, or placeholder.
            AIQuotaExhaustedError: If AI daily quota is exceeded.
            AIRateLimitError: If rate limit is hit and retries fail.
            AIServiceUnavailableError: If service returns 503 high demand after retries.
            AITimeoutError: If the request times out after retries.
            AIResponseError: For other permanent API errors or empty responses.
        """
        self._validate_configuration()

        # Sanitize model name: ensure prefix format models/... or just bare model
        clean_model = self.model
        if clean_model.startswith("models/"):
            clean_model = clean_model[len("models/"):]

        endpoint = f"{self.base_url}/models/{clean_model}:generateContent"

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        # Compatible with Gemini 3.8: no temperature, top_p, or top_k sampling parameters
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "maxOutputTokens": max_output_tokens,
            },
        }

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}],
            }

        data: Optional[Dict[str, Any]] = None
        total_attempts = 1 + self.max_retries
        total_start = time.perf_counter()

        for attempt in range(total_attempts):
            is_last_attempt = (attempt == self.max_retries)
            attempt_start = time.perf_counter()

            try:
                async with httpx.AsyncClient(timeout=self.timeout_config) as client:
                    response = await client.post(endpoint, json=payload, headers=headers)

                attempt_duration = time.perf_counter() - attempt_start

                # 1. Permanent Errors: Do NOT retry (400, 401, 403, 404)
                if response.status_code == 404:
                    logger.error(
                        "Gemini API model '%s' not found (HTTP 404 after %.2fs). Details: %s",
                        clean_model,
                        attempt_duration,
                        response.text[:200],
                    )
                    raise AIResponseError(
                        f"AI model '{clean_model}' was not found or is unavailable (HTTP 404). "
                        "Please verify the model name (e.g. gemini-3.8-flash)."
                    )

                if response.status_code in {400, 401, 403}:
                    logger.error(
                        "Gemini API permanent client error HTTP %d after %.2fs: %s",
                        response.status_code,
                        attempt_duration,
                        response.text[:200],
                    )
                    if response.status_code == 401:
                        raise MissingApiKeyError(
                            "Invalid or unauthorized AI API key (HTTP 401)."
                        )
                    raise AIResponseError(
                        f"AI service returned client error HTTP {response.status_code}."
                    )

                # 2. Rate Limiting (429): Check quota exhaustion vs temporary burst
                if response.status_code == 429:
                    body_lower = response.text.lower()
                    is_quota = any(
                        term in body_lower
                        for term in ["quota", "exhausted", "daily", "billing"]
                    )
                    if is_quota:
                        logger.warning(
                            "Gemini API daily/plan quota exhausted (HTTP 429 after %.2fs)",
                            attempt_duration,
                        )
                        raise AIQuotaExhaustedError("AI quota has been exhausted.")

                    if is_last_attempt:
                        logger.warning(
                            "Gemini API burst rate limit (HTTP 429) persisted after %d attempts (%.2fs)",
                            attempt + 1,
                            attempt_duration,
                        )
                        raise AIRateLimitError("AI service rate limit exceeded.")

                    delay = self._calculate_delay(response, attempt)
                    logger.info(
                        "Gemini API burst rate limit (HTTP 429 on attempt %d/%d, %.2fs). Retrying in %.1fs...",
                        attempt + 1,
                        total_attempts,
                        attempt_duration,
                        delay,
                    )
                    await self._sleep(delay)
                    continue

                # 3. Temporary Server Errors (500, 502, 503, 504)
                if response.status_code in {500, 502, 503, 504}:
                    if is_last_attempt:
                        logger.error(
                            "Gemini API error HTTP %d persisted after %d attempts (%.2fs): %s",
                            response.status_code,
                            attempt + 1,
                            attempt_duration,
                            response.text[:200],
                        )
                        if response.status_code == 503:
                            raise AIServiceUnavailableError(
                                "AI service is currently experiencing high demand (HTTP 503)."
                            )
                        raise AIResponseError(
                            f"AI service returned temporary error HTTP {response.status_code}."
                        )

                    delay = self._calculate_delay(response, attempt)
                    logger.warning(
                        "Gemini API HTTP %d on attempt %d/%d (took %.2fs). Retrying in %.1fs...",
                        response.status_code,
                        attempt + 1,
                        total_attempts,
                        attempt_duration,
                        delay,
                    )
                    await self._sleep(delay)
                    continue

                if response.is_error:
                    logger.error(
                        "Gemini API unhandled HTTP %d after %.2fs: %s",
                        response.status_code,
                        attempt_duration,
                        response.text[:200],
                    )
                    raise AIResponseError(
                        f"AI service returned HTTP {response.status_code}."
                    )

                data = response.json()
                logger.info(
                    "Gemini API generateContent succeeded on attempt %d/%d in %.2fs",
                    attempt + 1,
                    total_attempts,
                    attempt_duration,
                )
                break  # Successful response received, exit loop

            except httpx.TimeoutException as exc:
                attempt_duration = time.perf_counter() - attempt_start
                timeout_type = (
                    "ConnectTimeout (failed connecting to Google)"
                    if isinstance(exc, httpx.ConnectTimeout)
                    else "ReadTimeout (Google took longer than response timeout to generate)"
                    if isinstance(exc, httpx.ReadTimeout)
                    else f"{type(exc).__name__}"
                )

                if is_last_attempt:
                    total_elapsed = time.perf_counter() - total_start
                    logger.warning(
                        "Gemini API request failed with %s on final attempt %d/%d "
                        "(attempt duration: %.2fs, total time elapsed: %.2fs)",
                        timeout_type,
                        attempt + 1,
                        total_attempts,
                        attempt_duration,
                        total_elapsed,
                    )
                    raise AITimeoutError(
                        f"AI request timed out ({timeout_type} after {total_elapsed:.1f}s)."
                    ) from exc

                delay = self._calculate_delay(None, attempt)
                logger.warning(
                    "Gemini API %s on attempt %d/%d (after %.2fs). Retrying in %.1fs...",
                    timeout_type,
                    attempt + 1,
                    total_attempts,
                    attempt_duration,
                    delay,
                )
                await self._sleep(delay)
                continue

            except (AIError, MissingApiKeyError):
                raise

            except httpx.RequestError as exc:
                attempt_duration = time.perf_counter() - attempt_start
                if is_last_attempt:
                    logger.error(
                        "Network error connecting to Gemini API on final attempt %d/%d (%.2fs): %s",
                        attempt + 1,
                        total_attempts,
                        attempt_duration,
                        type(exc).__name__,
                    )
                    raise AIResponseError("Failed to connect to AI service.") from exc

                delay = self._calculate_delay(None, attempt)
                logger.warning(
                    "Network error (%s) on attempt %d/%d (%.2fs). Retrying in %.1fs...",
                    type(exc).__name__,
                    attempt + 1,
                    total_attempts,
                    attempt_duration,
                    delay,
                )
                await self._sleep(delay)
                continue

            except Exception as exc:
                attempt_duration = time.perf_counter() - attempt_start
                logger.error(
                    "Unexpected error in Gemini API call on attempt %d/%d (%.2fs): %s",
                    attempt + 1,
                    total_attempts,
                    attempt_duration,
                    exc,
                )
                raise AIResponseError(f"Unexpected error in AI service: {exc}") from exc

        if data is None:
            raise AIResponseError("AI service returned no response data.")

        # Extract text from response payload
        candidates = data.get("candidates")
        if not candidates or not isinstance(candidates, list):
            # Check for prompt feedback block
            feedback = data.get("promptFeedback", {})
            block_reason = feedback.get("blockReason")
            if block_reason:
                raise AIResponseError(f"AI response blocked by safety filter: {block_reason}")
            raise AIResponseError("AI service returned no candidates.")

        first_candidate = candidates[0]
        finish_reason = first_candidate.get("finishReason")
        if finish_reason == "SAFETY":
            raise AIResponseError("AI response was blocked by safety filters.")

        content = first_candidate.get("content", {})
        parts = content.get("parts", [])
        if not parts:
            raise AIResponseError("AI candidate contained no content parts.")

        text_part = parts[0].get("text", "")
        cleaned_text = text_part.strip()

        if not cleaned_text:
            raise AIResponseError("AI service generated an empty response.")

        return cleaned_text
