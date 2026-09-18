"""E-mail delivery.

Le serveur SMTP, le port et la sécurité sont figés dans le code pour Gmail
(config.SMTP_HOST / SMTP_PORT / SMTP_SECURE) : non modifiables depuis
l'interface. Seuls l'utilisateur et le mot de passe se règlent dans Paramètres
(table settings) ; l'expéditeur (From) est dérivé automatiquement de l'adresse
de connexion, ou d'un nom éventuellement encore stocké dans smtp_from pour
compatibilité. Sans identifiants, les messages restent uniquement dans la
boîte d'envoi (table outbox) et ce module renvoie False.
"""
import smtplib
import ssl
from email.message import EmailMessage

from . import database
from .config import env, get_setting


def _settings():
    host = (env.SMTP_HOST or "smtp.gmail.com").strip()
    port = int(env.SMTP_PORT or 587)
    port = port if port > 0 else 587
    user = (get_setting("smtp_user") or "").strip()
    pwd = get_setting("smtp_password") or ""
    raw_from = (get_setting("smtp_from") or "").strip()
    secure = (env.SMTP_SECURE or "tls").lower()
    if raw_from and "@" in raw_from:
        # Adresse complète ou "Nom <adresse@exemple.com>" saisi tel quel.
        sender = raw_from
    elif raw_from:
        # Nom seul saisi (ex. KPRIMESOFT_AUTOMATION) : le code lui associe
        # l'adresse Gmail de connexion pour que le mail soit valide et accepté.
        # Le destinataire voit le nom, l'adresse reste liée techniquement.
        address = user or "no-reply@automation.local"
        sender = f"{raw_from} <{address}>"
    else:
        sender = user or "no-reply@automation.local"
    return host, port, user, pwd, sender, secure


def send_email(to_email, subject, body_html, body_text=None, attachments=None):
    """Attempt delivery. Always records the message in the outbox table first.
    Returns True when sent over SMTP, False when only queued.

    `attachments` is an optional list of dicts:
    {"name": "rapport.pdf", "content": b"...", "maintype": "application", "subtype": "pdf"}
    """
    database.queue_outbox(to_email, subject, body_html)

    host, port, user, pwd, sender, secure = _settings()
    if not (host and user and pwd):
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(body_text or "Veuillez consulter cet e-mail avec un client compatible HTML.")
    msg.add_alternative(body_html, subtype="html")

    for att in (attachments or []):
        msg.add_attachment(att["content"],
                           maintype=att.get("maintype", "application"),
                           subtype=att.get("subtype", "octet-stream"),
                           filename=att.get("name", "rapport.pdf"))

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
        import traceback
        traceback.print_exc()
        return False