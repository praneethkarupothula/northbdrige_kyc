"""Outbound email. Without SMTP settings, mail is captured in the database for development only.

The development capture is clearly labelled in the UI ("Development mailbox") and the API
route that reads it is disabled in production.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import EmailOutbox
from app.observability.telemetry import get_logger, safe_error_class

log = get_logger()


def send_email(db: Session, to: str, subject: str, body: str) -> None:
    s = get_settings()
    if s.dev_email_capture:
        db.add(EmailOutbox(to_address=to, subject=subject, body=body, transport="dev-capture"))
        log.info("email_captured_for_development", status="captured")
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
            smtp.send_message(msg)
        db.add(EmailOutbox(to_address=to, subject=subject, body="(sent via SMTP; body not stored)", transport="smtp"))
        log.info("email_sent", status="sent")
    except Exception as exc:  # noqa: BLE001
        log.error("email_send_failed", error_class=safe_error_class(exc))
        raise
