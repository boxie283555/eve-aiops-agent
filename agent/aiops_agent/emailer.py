from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .settings import Settings


def send_report(settings: Settings, subject: str, body: str) -> bool:
    if not settings.send_email or not settings.smtp_host or not settings.smtp_to:
        return False

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = ", ".join(settings.smtp_to)
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
    return True

