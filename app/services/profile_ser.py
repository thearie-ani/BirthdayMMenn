from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.repositories.reminder_repository import ReminderRepository
from app.database.repositories.user_repository import UserRepository

DEFAULT_REMINDER_DAYS = (7, 1, 0)
DEFAULT_REMINDER_TIME = time(9, 0)


async def register_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    first_name: str | None,
    username: str | None,
    default_timezone: str,
) -> tuple[User, bool]:
    """Return (user, created). Safe to call on every /start."""
    users = UserRepository(session)

    existing = await users.get_by_telegram_id(telegram_id)
    if existing is not None:
        # Keep the stored display info fresh; no duplicate row is created.
        existing.first_name = first_name
        existing.username = username
        return existing, False

    user = await users.add(
        User(
            telegram_id=telegram_id,
            first_name=first_name,
            username=username,
            timezone=default_timezone,
        )
    )
    await ReminderRepository(session).add_default_settings(
        user.id, DEFAULT_REMINDER_DAYS, DEFAULT_REMINDER_TIME
    )
    return user, True