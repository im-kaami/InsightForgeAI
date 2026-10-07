import logging
import smtplib
import threading
from collections.abc import Callable
from email.message import EmailMessage

from sqlalchemy.orm import Session

from insightforge.config import get_settings
from insightforge.db.models import User

logger = logging.getLogger("insightforge")


class MailError(Exception):
    pass


def email_enabled() -> bool:
    settings = get_settings()
    return bool(settings.smtp_host and settings.smtp_from)


def send_email(to: str, subject: str, text: str) -> None:
    settings = get_settings()
    if not email_enabled():
        raise MailError("Email is not set up")
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    try:
        if settings.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
            )
        else:
            server = smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
            )
        with server:
            if settings.smtp_security == "starttls":
                server.starttls()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    except Exception as error:
        raise MailError(type(error).__name__) from error


def _send_quietly(to: str, subject: str, text: str) -> None:
    try:
        send_email(to, subject, text)
    except Exception as error:  # noqa: BLE001 - delivery problems must never break the caller
        logger.warning("email delivery failed: %s", type(error.__cause__ or error).__name__)


def _in_background(to: str, subject: str, text: str) -> None:
    threading.Thread(target=_send_quietly, args=(to, subject, text), daemon=True).start()


start_background: Callable[[str, str, str], None] = _in_background


def send_in_background(to: str, subject: str, text: str) -> None:
    start_background(to, subject, text)


def notify_user(db: Session, user_id: str, subject: str, text: str) -> None:
    """Email a user who turned alerts on; does nothing when email is off."""
    if not email_enabled():
        return
    user = db.get(User, user_id)
    if user is not None and user.email_alerts:
        send_in_background(user.email, subject, text)
