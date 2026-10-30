"""
Wish service managing business logic, ownership checks, and AI wish generation.
"""
import asyncio
import logging
from typing import Any, Dict, Optional
from sqlalchemy.engine import Engine

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
from app.ai.wish_generator import WishGenerator
from app.services.friend_ser import (
    FriendNotFoundError,
    UserNotRegisteredError,
    handle_get_friend_profile,
)

logger = logging.getLogger(__name__)


async def generate_wish_for_friend(
    telegram_id: int,
    friend_id: int,
    style: Optional[str] = None,
    variation_seed: Optional[int] = None,
    engine: Optional[Engine] = None,
    ai_client: Optional[GeminiClient] = None,
) -> Dict[str, Any]:
    """
    Retrieve friend profile ensuring ownership by the user, then generate a wish.

    Args:
        telegram_id: Telegram user ID requesting the wish.
        friend_id: Database ID of the friend.
        style: Optional style override chosen by user.
        variation_seed: Optional seed for generating alternate versions.
        engine: Optional target database engine.
        ai_client: Optional injected GeminiClient (for mocking/testing).

    Returns:
        Dict containing:
            - wish (str): The generated birthday wish text.
            - style (str): The canonical style applied.
            - friend (Dict[str, Any]): The friend's profile details.

    Raises:
        UserNotRegisteredError: If user is not registered in the database.
        FriendNotFoundError: If friend does not exist or belongs to another user.
        MissingApiKeyError: If AI API key is not configured.
        AIRateLimitError: If AI service rate limit is hit.
        AITimeoutError: If AI call times out.
        AIResponseError: For other AI generation or response errors.
    """
    # 1. Fetch friend profile and verify ownership (FriendNotFoundError if not owned)
    profile = await asyncio.to_thread(
        handle_get_friend_profile,
        telegram_id=telegram_id,
        friend_id=friend_id,
        engine=engine,
    )

    # 2. Instantiate wish generator with injected or default client
    generator = WishGenerator(client=ai_client)

    # 3. Generate wish
    wish_text, final_style = await generator.generate_birthday_wish(
        friend_profile=profile,
        style_override=style,
        variation_seed=variation_seed,
    )

    return {
        "wish": wish_text,
        "style": final_style,
        "friend": profile,
    }
