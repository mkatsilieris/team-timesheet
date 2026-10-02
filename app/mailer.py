"""Αποστολή email με SMTP. Χωρίς SMTP_HOST το μήνυμα γράφεται στο log του server (τοπικό development)."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger("uvicorn.error")

EMAIL_DOMAIN = "netcompany.com"


def app_url() -> str:
    return os.environ.get("APP_URL", "").strip().rstrip("/")


def send_email(to: str, subject: str, text: str, html: str) -> None:
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        log.warning("[DEV] Δεν έχει οριστεί SMTP_HOST. Email προς %s: %s\n%s", to, subject, text)
        return
    port = int(os.environ.get("SMTP_PORT", "25"))
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SMTP_FROM_EMAIL", f"no-reply@{EMAIL_DOMAIN}")
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(f'<div style="font-family:Arial,sans-serif;font-size:14px;color:#1d2528">{html}</div>',
                        subtype="html")

    if port == 465 or os.environ.get("SMTP_SSL", "") == "1":
        smtp = smtplib.SMTP_SSL(host, port, timeout=15)
    else:
        smtp = smtplib.SMTP(host, port, timeout=15)
    with smtp:
        if not isinstance(smtp, smtplib.SMTP_SSL) and (port == 587 or os.environ.get("SMTP_STARTTLS", "") == "1"):
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(msg)
