from collections.abc import Iterable
from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ReminderSetting


class ReminderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_default_settings(
        self, user_id: int, days_before: Iterable[int], reminder_time: time
    ) -> None:
        self._session.add_all(
            [
                ReminderSetting(
                    user_id=user_id,
                    days_before=days,
                    reminder_time=reminder_time,
                    enabled=True,
                )
                for days in days_before
            ]
        )
        await self._session.flush()