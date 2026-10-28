"""
Reminder repository for database operations on UserSetting and SentReminder entities.
"""
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.reminder import SentReminder, UserSetting
from app.utils.timezone import DEFAULT_REMINDER_TIME, DEFAULT_TIMEZONE


class ReminderRepository:
    """Repository handling database queries and persistence for reminder settings and logs."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_user_setting(self, user_id: int) -> Optional[UserSetting]:
        """
        Query user reminder settings by user database ID.

        Args:
            user_id: Database primary key ID of the user.

        Returns:
            UserSetting if found, or None.
        """
        statement = select(UserSetting).where(UserSetting.user_id == user_id)
        return self.session.execute(statement).scalar_one_or_none()

    def get_or_create_user_setting(self, user_id: int) -> UserSetting:
        """
        Query user reminder settings or initialize default settings if not yet present.

        Args:
            user_id: Database primary key ID of the user.

        Returns:
            UserSetting instance.
        """
        setting = self.get_user_setting(user_id)
        if setting is None:
            setting = UserSetting(
                user_id=user_id,
                remind_7_days=True,
                remind_1_day=True,
                remind_same_day=True,
                reminder_time=DEFAULT_REMINDER_TIME,
                timezone=DEFAULT_TIMEZONE,
                is_enabled=True,
            )
            self.session.add(setting)
            self.session.flush()
        return setting

    def is_reminder_sent(
        self, friend_id: int, celebration_year: int, reminder_type: int
    ) -> bool:
        """
        Check if a reminder has already been recorded as sent for a friend, year, and type.

        Args:
            friend_id: Database primary key of the friend.
            celebration_year: Year of the birthday celebration.
            reminder_type: Offset type (7, 1, or 0).

        Returns:
            True if previously sent, False otherwise.
        """
        statement = select(SentReminder).where(
            SentReminder.friend_id == friend_id,
            SentReminder.celebration_year == celebration_year,
            SentReminder.reminder_type == reminder_type,
        )
        return self.session.execute(statement).scalar_one_or_none() is not None

    def record_sent_reminder(
        self,
        user_id: int,
        friend_id: int,
        reminder_type: int,
        celebration_year: int,
    ) -> SentReminder:
        """
        Persist a delivery record after confirmed Telegram dispatch.

        Args:
            user_id: Owning user's primary key ID.
            friend_id: Friend's primary key ID.
            reminder_type: Offset type (7, 1, or 0).
            celebration_year: Year of the birthday celebration.

        Returns:
            The created SentReminder entity.
        """
        sent = SentReminder(
            user_id=user_id,
            friend_id=friend_id,
            reminder_type=reminder_type,
            celebration_year=celebration_year,
            sent_at=datetime.now(timezone.utc),
        )
        self.session.add(sent)
        self.session.flush()
        return sent
