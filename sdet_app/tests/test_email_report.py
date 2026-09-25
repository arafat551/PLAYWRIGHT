"""Tests for the e-mail report of finished test runs (statistics + PDF)."""

from sdet_app import config, database
from sdet_app import mailer
from sdet_app.sdet import email_report


class _FakeTest:
    id = 41
    project_name = "ProjetEmail"
    project_env = "STAGING"
    run_type = "Scénario"
    started_at = "2026-01-01 10:00:00"
    finished_at = "2026-01-01 10:01:05"
    launched_by = "admin@example.com"


_FAKE_RESULTS = [
    dict(module="CRM", function_name="Login", action="Ouvrir login",
         expected="Page login", obtained="", status="PASS",
         severity="", data="", screenshot=""),
    dict(module="CRM", function_name="Logout", action="Se déconnecter",
         expected="Page accueil", obtained="Timeout", status="FAIL",
         severity="MAJOR", data="", screenshot=""),
]


def _configure(**values):
    config.apply_settings(values)


def _clear_email_settings():
    config.reset_settings(["report_email_enabled", "report_email_recipient",
                           "smtp_host", "smtp_port", "smtp_user",
                           "smtp_password", "smtp_from", "smtp_secure"])


def test_sender_display_name_uses_configured_name(_init_db):
    _configure(smtp_user="mon.adresse@gmail.com", smtp_password="app-pwd",
               smtp_from="KPRIMESOFT_AUTOMATION")
    _host, _port, _user, _pwd, sender, _secure = mailer._settings()
    assert sender == "KPRIMESOFT_AUTOMATION <mon.adresse@gmail.com>"

    _configure(smtp_from="")
    _host, _port, _user, _pwd, sender, _secure = mailer._settings()
    assert sender == "mon.adresse@gmail.com"

    _configure(smtp_from="Équipe QA <qa@domaine.test>")
    _host, _port, _user, _pwd, sender, _secure = mailer._settings()
    assert sender == "Équipe QA <qa@domaine.test>"
    _clear_email_settings()


def test_build_email_content_contains_stats_table():
    report = {"project": "P", "environment": "STAGING", "run_type": "Scénario",
              "date": "2026-01-01 10:00:00", "launched_by": "admin@x.test",
              "duration": "1m 05s", "total": 5, "passed": 3, "failed": 1,
              "warning": 1, "skipped": 0, "success_pct": 60.0,
              "module_stats": [{"name": "CRM", "total": 5, "pass": 3, "fail": 1,
                                "warning": 1, "skipped": 0}],
              "failures": [{"module": "CRM", "function": "Logout",
                            "obtained": "Timeout"}]}
    subject, body_text, body_html = email_report.build_email_content(
        {"name": "P", "environment": "STAGING"}, {"run_type": "Scénario",
        "started_at": "2026-01-01", "launched_by": "admin@x.test",
        "duration": "1m 05s"}, report)

    assert "P" in subject
    assert "60.0%" in subject
    for token in ("Total", "PASS", "FAIL", "WARNING", "SKIPPED",
                  "ÉCHEC", "Logout", "Timeout"):
        assert token in body_text
    assert "Total   : 5" in body_text
    assert "PASS    : 3" in body_text
    assert "<img" not in body_html and "<video" not in body_html, \
        "l'e-mail ne doit contenir aucune image"
    assert "<table" in body_html
    assert "Rapport d'exécution AUTOMATION" in body_html


def test_email_verdict_marks_cancelled_run():
    report = {"project": "P", "environment": "STAGING", "run_type": "Scénario",
              "date": "2026-01-01", "launched_by": "admin@x.test",
              "duration": "0m 00s", "total": 0, "passed": 0, "failed": 0,
              "warning": 0, "skipped": 0, "success_pct": 0.0,
              "status": "cancelled", "module_stats": [], "failures": []}
    _subject, body_text, _body_html = email_report.build_email_content(
        {"name": "P"}, {"run_type": "Scénario", "started_at": "2026-01-01",
                         "launched_by": "admin@x.test", "duration": "0m 00s"},
        report)
    assert "ANNULÉ" in body_text


def test_send_run_report_disabled_is_noop(_init_db, monkeypatch):
    _configure(report_email_enabled="0", report_email_recipient="qa@x.test")
    calls = []
    monkeypatch.setattr(mailer, "send_email",
                        lambda *a, **k: calls.append((a, k)) or True)
    sent, recipients = email_report.send_run_report(999)
    assert sent == 0
    assert recipients == []
    assert calls == []
    _clear_email_settings()


class _CapturingSMTP:
    """Stand-in for smtplib.SMTP that keeps whatever message is submitted."""

    sent_messages = []

    def __init__(self, host=None, port=None, timeout=15):
        self.host = host

    def starttls(self):
        pass

    def login(self, user, pwd):
        pass

    def send_message(self, msg):
        self.sent_messages.append(msg)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_send_email_actually_attaches_the_pdf(_init_db, monkeypatch):
    """Regression: the PDF must reach the message handed to the SMTP server,
    otherwise the recipient receives the e-mail without the attachment."""
    import smtplib

    _configure(report_email_enabled="1",
               report_email_recipient="qa@x.test",
               smtp_user="mon.adresse@gmail.com", smtp_password="app-pwd",
               smtp_from="")
    _CapturingSMTP.sent_messages = []
    monkeypatch.setattr(smtplib, "SMTP", _CapturingSMTP)

    subject, body_text, body_html = email_report.build_email_content(
        {"name": "P", "url": "https://proj.test", "environment": "STAGING"},
        {"run_type": "Scénario", "started_at": "2026-01-01",
         "launched_by": "admin@x.test", "duration": "1m 05s"},
        {"project": "P", "project_url": "https://proj.test",
         "environment": "STAGING", "run_type": "Scénario",
         "date": "2026-01-01", "launched_by": "admin@x.test",
         "duration": "1m 05s", "total": 5, "passed": 3, "failed": 1,
         "warning": 1, "skipped": 0, "success_pct": 60.0,
         "module_stats": [], "failures": []})

    attachments = [{"name": "rapport_P_41.pdf",
                    "content": b"%PDF-1.4 fake-bytes",
                    "maintype": "application", "subtype": "pdf"}]
    ok = mailer.send_email("qa@x.test", subject, body_html,
                           body_text=body_text, attachments=attachments)
    assert ok is True
    assert len(_CapturingSMTP.sent_messages) == 1
    msg = _CapturingSMTP.sent_messages[0]
    assert msg.get_content_maintype() == "multipart"
    got_attachments = list(msg.iter_attachments())
    assert len(got_attachments) == 1
    att = got_attachments[0]
    assert att.get_filename() == "rapport_P_41.pdf"
    assert (att.get("Content-Disposition") or "").lower().startswith("attachment")
    assert att.get_content() == b"%PDF-1.4 fake-bytes"
    _clear_email_settings()


def test_send_run_report_sends_pdf_to_recipients(_init_db, monkeypatch):
    _configure(report_email_enabled="1",
               report_email_recipient="qa@x.test; lead@y.test",
               smtp_host="smtp.test")
    from sdet_app.models import TestResult
    monkeypatch.setattr(database, "get_test_run", lambda tid: _FakeTest())
    monkeypatch.setattr(database, "list_results",
                        lambda tid: [TestResult(id=1, **r) for r in _FAKE_RESULTS])
    sent_mails = []
    monkeypatch.setattr(mailer, "send_email",
                        lambda *a, **k: sent_mails.append((a, k)) or True)
    monkeypatch.setattr(email_report, "render_report_pdf",
                        lambda html: b"%PDF-1.4 fake")

    sent, recipients = email_report.send_run_report(41)
    assert sent == 2
    assert recipients == ["qa@x.test", "lead@y.test"]
    assert [m[0][0] for m in sent_mails] == ["qa@x.test", "lead@y.test"]
    args, kwargs = sent_mails[0]
    body_html = args[2]
    body_text = kwargs["body_text"]
    assert "Rapport d'exécution AUTOMATION" in body_html
    assert "<table" in body_html
    assert "FAIL      : 1" in body_text or "FAIL    : 1" in body_text
    for _, kwargs in sent_mails:
        assert len(kwargs["attachments"]) == 1
        att = kwargs["attachments"][0]
        assert att["name"].endswith("_41.pdf")
        assert att["content"] == b"%PDF-1.4 fake"
        assert att["subtype"] == "pdf"
    _clear_email_settings()


def test_send_run_report_uses_project_recipients_when_set(_init_db, monkeypatch):
    """Les destinataires configurés sur le projet priment sur le global."""
    from sdet_app.models import TestResult

    _configure(report_email_enabled="1",
               report_email_recipient="global@x.test")

    class _ProjTest(_FakeTest):
        report_recipients = "equipe-projet@x.test; chef@x.test"

    monkeypatch.setattr(database, "get_test_run", lambda tid: _ProjTest())
    monkeypatch.setattr(database, "list_results",
                        lambda tid: [TestResult(id=1, **r) for r in _FAKE_RESULTS])
    monkeypatch.setattr(email_report, "render_report_pdf",
                        lambda html: b"%PDF-1.4 fake")
    sent_mails = []
    monkeypatch.setattr(mailer, "send_email",
                        lambda *a, **k: sent_mails.append((a, k)) or True)

    sent, recipients = email_report.send_run_report(41)
    assert sent == 2
    assert recipients == ["equipe-projet@x.test", "chef@x.test"]
    assert "global@x.test" not in recipients
    _clear_email_settings()


def test_send_run_report_falls_back_to_global_recipients(_init_db, monkeypatch):
    """Sans destinataires sur le projet, la valeur globale des Paramètres est utilisée."""
    from sdet_app.models import TestResult

    _configure(report_email_enabled="1", report_email_recipient="global@x.test")

    monkeypatch.setattr(database, "get_test_run", lambda tid: _FakeTest())
    monkeypatch.setattr(database, "list_results",
                        lambda tid: [TestResult(id=1, **r) for r in _FAKE_RESULTS])
    monkeypatch.setattr(email_report, "render_report_pdf",
                        lambda html: b"%PDF-1.4 fake")
    sent_mails = []
    monkeypatch.setattr(mailer, "send_email",
                        lambda *a, **k: sent_mails.append((a, k)) or True)

    sent, recipients = email_report.send_run_report(41)
    assert sent == 1
    assert recipients == ["global@x.test"]
    _clear_email_settings()


def test_settings_ui_saves_smtp_and_email_fields(client, _init_db):
    client.post("/login", data={"email": "admin@example.com", "password": "admin123"})
    r = client.post("/settings", data={
        "max_pages": "60", "max_buttons": "300", "max_forms": "30",
        "page_timeout": "30000", "headless": "0",
        "report_email_enabled": "1",
        "report_email_recipient": "rapports@x.test",
        "smtp_host": "smtp.gmail.com", "smtp_port": "587",
        "smtp_user": "user@x.test", "smtp_password": "secret",
        "smtp_from": "automation@x.test", "smtp_secure": "tls",
        "ai_model": ""})
    assert r.status_code == 302
    assert config.get_setting("report_email_enabled", "0") == "1"
    assert config.get_setting("smtp_host", "") == "smtp.gmail.com"
    assert config.get_setting("smtp_port", "") == "587"
    assert config.get_setting("report_email_recipient", "") == "rapports@x.test"

    html = client.get("/settings").get_data(as_text=True)
    # Serveur / port / sécurité sont figés dans le code : pas visibles dans l'interface.
    assert 'value="smtp.gmail.com"' not in html
    assert "Serveur SMTP" not in html
    assert "Port SMTP" not in html
    assert "Sécurité SMTP" not in html
    assert 'value="rapports@x.test"' in html
    assert "Rapport par e-mail" in html
    _clear_email_settings()