"""Tests for automatic OTP recovery from the mailbox (IMAP / 2FA)."""

from email.message import EmailMessage

from sdet_app import config
from sdet_app.sdet import mailbox


def _configure(**values):
    config.apply_settings(values)


def _clear_email_settings():
    config.reset_settings(["smtp_user", "smtp_password",
                           "imap_user", "imap_password"])


def _raw_mail(subject, html):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = "user@x.test"
    msg.set_content("Merci de saisir le code reçu dans l'application.")
    msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


# ---------------------------------------------------------------------------
# extract_otp
# ---------------------------------------------------------------------------

def test_extract_otp_from_html_body():
    html = "<p>Bonjour,</p><p>Votre code de vérification est <b>482913</b>.</p>"
    assert mailbox.extract_otp(text="", html=html) == "482913"


def test_extract_otp_from_subject():
    assert mailbox.extract_otp(subject="Votre code AUTOMATION est 482913") == "482913"


def test_extract_otp_no_code_returns_none():
    assert mailbox.extract_otp(subject="Bienvenue", text="Aucun chiffre ici") is None


def test_extract_otp_ignores_long_numbers():
    html = "Contactez le 0123456789 pour toute question."
    assert mailbox.extract_otp(text="", html=html) is None


def test_extract_otp_prefers_code_near_keyword():
    html = ("Votre numéro de client préféré est 123456. "
            "Votre code de vérification est 987654.")
    assert mailbox.extract_otp(text="", html=html) == "987654"


def test_extract_otp_last_resort_any_isolated_code():
    html = "Saisissez simplement 375264 sur le site."
    assert mailbox.extract_otp(text="", html=html) == "375264"


def test_extract_otp_english_body():
    html = "Your one-time code is <strong>884200</strong>. It expires in 5 minutes."
    assert mailbox.extract_otp(text="", html=html) == "884200"


# ---------------------------------------------------------------------------
# IMAP handled with a fake connection (no network)
# ---------------------------------------------------------------------------

class _FakeIMAP:
    def __init__(self, *a, **k):
        self.raw_message = _raw_mail(
            "Votre code de vérification",
            "<p>Votre code de vérification est : <b>719365</b></p>")

    def login(self, user, pwd):
        self.user = user
        self.pwd = pwd

    def select(self, _box):
        return ("OK", [b"1"])

    def search(self, charset, *criteria):
        if criteria and criteria[-1] == "ALL":
            return ("OK", [b"1"])
        return ("OK", [b""])

    def fetch(self, mid, _parts):
        # Miroir du comportement réel d'imaplib : un identifiant non décodé
        # (str(bytes) -> "b'1'") doit être rejeté.
        if not isinstance(mid, str) or not mid.isdigit():
            raise mailbox.imaplib.error(
                "FETCH command error: BAD [b'Could not parse command']")
        return ("OK", [(b"1", self.raw_message)])

    def logout(self):
        pass


def test_fetch_otp_code_via_fake_imap(_init_db, monkeypatch):
    _configure(imap_user="imap@x.test", imap_password="secret")
    calls = []
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", lambda *a, **k: calls.append((a, k)) or _FakeIMAP())

    code = mailbox.fetch_otp_code(recipient="user@x.test", timeout=30)
    assert code == "719365"
    assert calls, "IMAP doit être appelé"
    assert calls[0][0][0] == mailbox.IMAP_HOST
    _clear_email_settings()


def test_fetch_otp_code_falls_back_to_smtp_credentials(_init_db):
    _configure(smtp_user="mon.adresse@gmail.com", smtp_password="app-pwd")
    assert mailbox._credentials() == ("mon.adresse@gmail.com", "app-pwd")
    _clear_email_settings()


def test_fetch_otp_code_without_credentials_returns_none(_init_db):
    _clear_email_settings()
    assert mailbox.fetch_otp_code(recipient="user@x.test", timeout=10) is None