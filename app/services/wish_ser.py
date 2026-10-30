"""
Wish service managing business logic, persistence, ownership checks,
approval, sending, editing, and AI wish generation.
"""
import asyncio
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional
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
from app.database.database import get_session
from app.database.models.wish import Wish, WishStatus
from app.database.repositories.friend_repo import FriendRepository
from app.database.repositories.user_repo import UserRepository
from app.database.repositories.wish_repo import WishRepository
from app.services.friend_ser import (
    FriendNotFoundError,
    UserNotRegisteredError,
    handle_get_friend_profile,
)

logger = logging.getLogger(__name__)


class WishNotFoundError(Exception):
    """Raised when a requested wish cannot be found or does not belong to the user."""
    pass


class WishNotApprovedError(Exception):
    """Raised when attempting to send an unapproved wish."""
    pass


class EmptyWishContentError(ValueError):
    """Raised when user submits an empty or whitespace-only edited wish."""
    pass


def _format_wish_dict(wish: Wish) -> Dict[str, Any]:
    """Helper to convert a Wish SQLAlchemy model into a clean dictionary."""
    friend_name = wish.friend.name if wish.friend else "Friend"
    friend_telegram_id = getattr(wish.friend, "telegram_id", None) if wish.friend else None
    return {
        "wish_id": wish.id,
        "id": wish.id,
        "user_id": wish.user_id,
        "friend_id": wish.friend_id,
        "friend_name": friend_name,
        "friend_telegram_id": friend_telegram_id,
        "content": wish.content,
        "wish": wish.content,
        "style": wish.style,
        "status": wish.status,
        "created_at": wish.created_at,
        "updated_at": wish.updated_at,
    }


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
    Kept for backward compatibility with Stage 10.
    """
    profile = await asyncio.to_thread(
        handle_get_friend_profile,
        telegram_id=telegram_id,
        friend_id=friend_id,
        engine=engine,
    )

    generator = WishGenerator(client=ai_client)

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


async def create_or_regenerate_wish(
    telegram_id: int,
    friend_id: int,
    existing_wish_id: Optional[int] = None,
    style: Optional[str] = None,
    variation_seed: Optional[int] = None,
    engine: Optional[Engine] = None,
    ai_client: Optional[GeminiClient] = None,
) -> Dict[str, Any]:
    """
    Generate an AI wish and persist it as a DRAFT.
    If existing_wish_id is provided and currently DRAFT, updates that record
    to avoid creating duplicate history entries accidentally.
    """
    # 1. Fetch friend profile and verify ownership
    profile = await asyncio.to_thread(
        handle_get_friend_profile,
        telegram_id=telegram_id,
        friend_id=friend_id,
        engine=engine,
    )

    # 2. Generate wish text via AI
    generator = WishGenerator(client=ai_client)
    wish_text, final_style = await generator.generate_birthday_wish(
        friend_profile=profile,
        style_override=style,
        variation_seed=variation_seed,
    )

    # 3. Persist in database
    def _persist() -> Dict[str, Any]:
        with get_session(engine) as session:
            user_repo = UserRepository(session)
            wish_repo = WishRepository(session)

            user = user_repo.get_by_telegram_id(telegram_id)
            if not user:
                raise UserNotRegisteredError(f"User {telegram_id} not registered.")

            wish = None
            if existing_wish_id:
                existing = wish_repo.get_by_id(wish_id=existing_wish_id, user_id=user.id)
                if existing and existing.status == WishStatus.DRAFT:
                    wish = wish_repo.update_draft(
                        wish_id=existing_wish_id,
                        user_id=user.id,
                        content=wish_text,
                        style=final_style,
                    )

            if wish is None:
                wish = wish_repo.create(
                    user_id=user.id,
                    friend_id=friend_id,
                    content=wish_text,
                    style=final_style,
                    status=WishStatus.DRAFT,
                )

            res = _format_wish_dict(wish)
            res["friend"] = profile
            return res

    return await asyncio.to_thread(_persist)


def handle_edit_wish(
    telegram_id: int,
    wish_id: int,
    new_content: str,
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    """
    Update the content of an existing wish. Validates non-empty content
    and resets status to DRAFT so edited content can be re-approved.
    """
    cleaned = (new_content or "").strip()
    if not cleaned:
        raise EmptyWishContentError("Birthday wish cannot be empty.")

    with get_session(engine) as session:
        user_repo = UserRepository(session)
        wish_repo = WishRepository(session)

        user = user_repo.get_by_telegram_id(telegram_id)
        if not user:
            raise UserNotRegisteredError(f"User {telegram_id} not registered.")

        wish = wish_repo.get_by_id(wish_id=wish_id, user_id=user.id)
        if not wish:
            raise WishNotFoundError(f"Wish {wish_id} not found or access denied.")

        wish_repo.update_content(
            wish_id=wish_id,
            user_id=user.id,
            content=cleaned,
            reset_status=True,
        )
        return _format_wish_dict(wish)


def handle_approve_wish(
    telegram_id: int,
    wish_id: int,
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    """
    Transition a wish from DRAFT to APPROVED status.
    """
    with get_session(engine) as session:
        user_repo = UserRepository(session)
        wish_repo = WishRepository(session)

        user = user_repo.get_by_telegram_id(telegram_id)
        if not user:
            raise UserNotRegisteredError(f"User {telegram_id} not registered.")

        wish = wish_repo.get_by_id(wish_id=wish_id, user_id=user.id)
        if not wish:
            raise WishNotFoundError(f"Wish {wish_id} not found or access denied.")

        wish_repo.update_status(wish_id=wish_id, user_id=user.id, status=WishStatus.APPROVED)
        return _format_wish_dict(wish)


async def handle_send_wish(
    telegram_id: int,
    wish_id: int,
    bot: Any = None,
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    """
    Send an approved wish to the friend's linked Telegram chat if available.
    Enforces that wish must be APPROVED. Never assumes unlinked friends can receive.
    """
    def _get_and_validate() -> Dict[str, Any]:
        with get_session(engine) as session:
            user_repo = UserRepository(session)
            wish_repo = WishRepository(session)

            user = user_repo.get_by_telegram_id(telegram_id)
            if not user:
                raise UserNotRegisteredError(f"User {telegram_id} not registered.")

            wish = wish_repo.get_by_id(wish_id=wish_id, user_id=user.id)
            if not wish:
                raise WishNotFoundError(f"Wish {wish_id} not found or access denied.")

            if wish.status != WishStatus.APPROVED and wish.status != WishStatus.SENT:
                raise WishNotApprovedError("Only approved wishes can be sent. Please approve the wish first.")

            return _format_wish_dict(wish)

    wish_data = await asyncio.to_thread(_get_and_validate)

    recipient_telegram_id = wish_data.get("friend_telegram_id")
    friend_name = wish_data.get("friend_name", "Friend")

    # If recipient is not linked, explain to user
    if not recipient_telegram_id:
        return {
            "sent": False,
            "reason": "unlinked",
            "friend_name": friend_name,
            "wish_id": wish_id,
            "content": wish_data["content"],
            "status": wish_data["status"],
        }

    # Attempt to send message via Telegram Bot API
    if bot:
        try:
            res = bot.send_message(chat_id=recipient_telegram_id, text=wish_data["content"])
            if hasattr(res, "__await__"):
                await res

            def _mark_sent():
                with get_session(engine) as session:
                    user_repo = UserRepository(session)
                    wish_repo = WishRepository(session)
                    user = user_repo.get_by_telegram_id(telegram_id)
                    if user:
                        wish_repo.update_status(wish_id=wish_id, user_id=user.id, status=WishStatus.SENT)

            await asyncio.to_thread(_mark_sent)

            wish_data["status"] = WishStatus.SENT
            return {
                "sent": True,
                "friend_name": friend_name,
                "wish_id": wish_id,
                "content": wish_data["content"],
                "status": WishStatus.SENT,
            }
        except Exception as err:
            logger.warning("Failed to send Telegram message to recipient %s: %s", recipient_telegram_id, err)
            return {
                "sent": False,
                "reason": "delivery_failed",
                "error": str(err),
                "friend_name": friend_name,
                "wish_id": wish_id,
                "content": wish_data["content"],
                "status": wish_data["status"],
            }

    return {
        "sent": False,
        "reason": "bot_unavailable",
        "friend_name": friend_name,
        "wish_id": wish_id,
        "content": wish_data["content"],
        "status": wish_data["status"],
    }


def handle_get_wish(
    telegram_id: int,
    wish_id: int,
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    """
    Retrieve single wish ensuring user ownership.
    """
    with get_session(engine) as session:
        user_repo = UserRepository(session)
        wish_repo = WishRepository(session)

        user = user_repo.get_by_telegram_id(telegram_id)
        if not user:
            raise UserNotRegisteredError(f"User {telegram_id} not registered.")

        wish = wish_repo.get_by_id(wish_id=wish_id, user_id=user.id)
        if not wish:
            raise WishNotFoundError(f"Wish {wish_id} not found or access denied.")

        return _format_wish_dict(wish)


def handle_get_user_wishes(
    telegram_id: int,
    page: int = 1,
    page_size: int = 5,
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    """
    Retrieve paginated birthday wishes belonging strictly to the requesting user.
    """
    with get_session(engine) as session:
        user_repo = UserRepository(session)
        wish_repo = WishRepository(session)

        user = user_repo.get_by_telegram_id(telegram_id)
        if not user:
            raise UserNotRegisteredError(f"User {telegram_id} not registered.")

        total_count = wish_repo.count_user_wishes(user_id=user.id)
        total_pages = max(1, math.ceil(total_count / page_size))
        current_page = max(1, min(page, total_pages))
        offset = (current_page - 1) * page_size

        wishes = wish_repo.get_user_wishes(
            user_id=user.id,
            limit=page_size,
            offset=offset,
        )

        items = [_format_wish_dict(w) for w in wishes]
        return {
            "items": items,
            "page": current_page,
            "total_pages": total_pages,
            "total_count": total_count,
        }


def handle_link_friend_telegram(
    telegram_id: int,
    friend_id: int,
    friend_telegram_id: Optional[int],
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    """
    Link a friend's Telegram ID to enable direct message delivery upon wish approval.
    """
    with get_session(engine) as session:
        user_repo = UserRepository(session)
        friend_repo = FriendRepository(session)

        user = user_repo.get_by_telegram_id(telegram_id)
        if not user:
            raise UserNotRegisteredError(f"User {telegram_id} not registered.")

        friend = friend_repo.get_by_user_and_id(user_id=user.id, friend_id=friend_id)
        if not friend:
            raise FriendNotFoundError(f"Friend {friend_id} not found.")

        friend_repo.update_telegram_id(friend_id=friend_id, telegram_id=friend_telegram_id)
        return {
            "friend_id": friend.id,
            "name": friend.name,
            "telegram_id": friend.telegram_id,
        }
