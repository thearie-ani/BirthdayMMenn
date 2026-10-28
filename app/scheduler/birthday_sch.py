"""
Scheduler module running periodic scans to deliver automated birthday reminders.
"""
import asyncio
import logging
from typing import Optional
from telegram.error import Forbidden, TelegramError
from telegram.ext import Application, ContextTypes

from app.services.reminder_ser import (
    get_due_reminders_for_all_users,
    record_sent_reminder,
)

logger = logging.getLogger(__name__)

JOB_NAME = "birthday_reminder_periodic_scan"
DEFAULT_SCAN_INTERVAL_SECONDS = 900  # Scan every 15 minutes


async def check_and_send_birthday_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Scheduled job callback. Evaluates due reminders, delivers Telegram messages,
    and records persistent deduplication records upon successful delivery.
    """
    engine = None
    now_override = None
    if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict):
        engine = context.bot_data.get("db_engine")
        now_override = context.bot_data.get("now_override")

    try:
        due_items = await asyncio.to_thread(
            get_due_reminders_for_all_users,
            engine=engine,
            now_override=now_override,
        )
    except Exception as err:
        logger.error("Failed to query due birthday reminders: %s", err)
        return

    if not due_items:
        return

    logger.info("Found %d due birthday reminders to deliver", len(due_items))

    for item in due_items:
        try:
            # 1. Dispatch message to Telegram user
            await context.bot.send_message(
                chat_id=item["telegram_id"],
                text=item["message_text"],
            )

            # 2. Telegram confirmed delivery! Record persistent deduplication log
            await asyncio.to_thread(
                record_sent_reminder,
                user_id=item["user_id"],
                friend_id=item["friend_id"],
                reminder_type=item["reminder_type"],
                celebration_year=item["celebration_year"],
                engine=engine,
            )
            logger.info(
                "Successfully delivered reminder type=%d for friend_id=%d to user telegram_id=%d",
                item["reminder_type"],
                item["friend_id"],
                item["telegram_id"],
            )
        except Forbidden:
            logger.warning(
                "User telegram_id=%d has blocked the bot or chat is forbidden. Reminder not logged as sent.",
                item["telegram_id"],
            )
        except TelegramError as err:
            logger.error(
                "TelegramError delivering reminder to user telegram_id=%d: %s",
                item["telegram_id"],
                err,
            )
        except Exception as err:
            logger.error(
                "Unexpected error delivering reminder to user telegram_id=%d: %s",
                item["telegram_id"],
                err,
            )


def setup_birthday_scheduler(
    application: Application,
    scan_interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS,
) -> None:
    """
    Register the periodic reminder scanning task with python-telegram-bot's JobQueue.

    Args:
        application: The Telegram Application instance.
        scan_interval_seconds: Polling interval in seconds (default 900 = 15 minutes).
    """
    if application.job_queue is None:
        logger.warning("JobQueue is not enabled on Application; reminders will not run.")
        return

    # Prevent accidental duplicate registration
    existing_jobs = application.job_queue.get_jobs_by_name(JOB_NAME)
    if existing_jobs:
        logger.info("Periodic reminder job '%s' is already registered.", JOB_NAME)
        return

    application.job_queue.run_repeating(
        callback=check_and_send_birthday_reminders,
        interval=scan_interval_seconds,
        first=10,
        name=JOB_NAME,
    )
    logger.info(
        "Registered periodic birthday reminder job '%s' with %d-second interval.",
        JOB_NAME,
        scan_interval_seconds,
    )
