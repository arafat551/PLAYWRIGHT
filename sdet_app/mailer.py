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


def render_html(title, content_html, accent="#4f46e5", preheader=""):
    """Wrap inner HTML into a polished, responsive e-mail document.

    Table-based layout (Outlook/Gmail/Apple iOS safe), no images: relies only
    on inline styles. `title` is displayed in the gradient header;
    `content_html` is the body of the white card; `preheader` (optional) shows
    up as the snippet in the recipient's mailbox. `accent` drives the header.
    """
    pre = (preheader or "").strip()
    return f"""<!DOCTYPE html>
<html lang="fr" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>{title}</title>
<!--[if mso]>
<xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml>
<![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#eef2f8;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<!-- Preheader : extrait visible dans la boîte de réception -->
<div style="display:none;font-size:1px;color:#eef2f8;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{pre}</div>
<!--[if mso]><table role="presentation" width="560" align="center" cellpadding="0" cellspacing="0"><tr><td><![endif]-->
<div style="max-width:560px;margin:0 auto;padding:14px 10px 20px;font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
    <!-- Bandeau signature -->
    <tr>
      <td bgcolor="{accent}" style="background-color:{accent};background-image:linear-gradient(135deg,{accent} 0%,#7c3aed 60%,#a855f7 100%);border-radius:14px 14px 0 0;padding:20px 26px 16px;text-align:center;">
        <div style="color:#ffffff;font-size:18px;line-height:1.3;font-weight:800;letter-spacing:.2px;">{title}</div>
      </td>
    </tr>
    <!-- Carte blanche -->
    <tr>
      <td style="background:#ffffff;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 14px 14px;padding:22px 26px;font-size:13.5px;line-height:1.6;color:#334155;">
        {content_html}
      </td>
    </tr>
    <!-- Pied de page -->
    <tr>
      <td style="padding:12px 10px 0;text-align:center;font-size:10px;line-height:1.6;color:#94a3b8;">
        <div style="letter-spacing:1.2px;font-weight:700;color:#64748b;font-size:10px;">PLATEFORME AUTOMATION</div>
        <div style="margin-top:2px;">Ce message a été généré automatiquement — merci de ne pas y répondre.</div>
      </td>
    </tr>
  </table>
</div>
<!--[if mso]></td></tr></table><![endif]-->
</body>
</html>"""


def action_button(label, url, bg="#4f46e5"):
    """Bulletproof (Outlook-safe) centered call-to-action button."""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;margin:6px 0;"><tr><td align="center">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
        f'<tr><td style="border-radius:12px;background:{bg};">'
        f'<a href="{url}" '
        f'style="display:inline-block;padding:14px 30px;font-family:\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:12px;'
        f'background:{bg};box-shadow:0 8px 18px rgba(79,70,229,.30);">{label}</a>'
        f'</td></tr></table></td></tr></table>')


def info_note(text, kind="info"):
    """Small callout box (info | warning | danger | success) inside e-mails."""
    styles = {
        "info": ("#eef2ff", "#c7d2fe", "#3730a3", "&#9432;&nbsp;"),
        "warning": ("#fffbeb", "#fde68a", "#92400e", "&#9201;&nbsp;"),
        "danger": ("#fef2f2", "#fecaca", "#991b1b", "&#9888;&nbsp;"),
        "success": ("#f0fdf4", "#bbf7d0", "#166534", "&#10004;&nbsp;"),
    }
    bg, border, txt, icon = styles.get(kind, styles["info"])
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;margin:0 0 20px;"><tr>'
        f'<td style="background:{bg};border:1px solid {border};border-radius:12px;'
        f'padding:12px 16px;font-size:13px;line-height:1.6;color:{txt};">'
        f'{icon}&nbsp; {text}</td></tr></table>')