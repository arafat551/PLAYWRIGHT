"""E-mail delivery.

Reads the SMTP settings stored in the settings table (Paramètres > SMTP) with
.env style access via get_setting(). When no SMTP host is configured, messages
are only queued in the outbox table (inspectable in dev / by the tests) and
this module returns False.
"""
import smtplib
import ssl
from email.message import EmailMessage

from . import database
from .config import get_setting


def _settings():
    host = (get_setting("smtp_host") or "").strip()
    port = int(get_setting("smtp_port") or "587")
    port = port if port > 0 else 587
    user = (get_setting("smtp_user") or "").strip()
    pwd = get_setting("smtp_password") or ""
    sender = (get_setting("smtp_from") or "").strip() or user or "no-reply@automation.local"
    secure = (get_setting("smtp_secure") or "tls").lower()
    return host, port, user, pwd, sender, secure


def send_email(to_email, subject, body_html, body_text=None):
    """Attempt delivery. Always records the message in the outbox table first.
    Returns True when sent over SMTP, False when only queued."""
    database.queue_outbox(to_email, subject, body_html)

    host, port, user, pwd, sender, secure = _settings()
    if not host:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(body_text or "Veuillez consulter cet e-mail avec un client compatible HTML.")
    msg.add_alternative(body_html, subtype="html")

    try:
        if secure == "ssl":
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=15, context=ctx) as smtp:
                if user:
                    smtp.login(user, pwd)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.starttls()
                if user:
                    smtp.login(user, pwd)
                smtp.send_message(msg)
        return True
    except Exception:
        return False