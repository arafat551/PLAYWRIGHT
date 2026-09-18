"""Playwright browser management: launch, login, HTTP + JS monitoring.

This module owns the browser lifecycle. Other SDET modules (explorer,
runner) receive an open BrowserSession and drive it.
"""
import time

from ..config import env, setting_bool, setting_int


LOGIN_SELECTORS = {
    "email": 'input[type="email"], input[name*="email" i], input[autocomplete="username"], input[name="username"], input[name="login"], input[placeholder*="mail" i]',
    "password": 'input[type="password"]',
    "otp": 'input[name*="otp" i], input[name*="code" i], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[placeholder*="code" i], input[type="number"]',
    "submit": 'button[type="submit"], input[type="submit"], button:has-text("Se connecter"), button:has-text("Connexion"), button:has-text("Login"), button:has-text("Sign in"), button:has-text("Continuer")',
}

CRITICAL_HTTP = {400, 401, 403, 404, 422, 500, 502, 503}


class BrowserSession:
    """Encapsulates an open browser + page with shared listeners."""

    def __init__(self, playwright, headless=None):
        self.pw = playwright
        headless = setting_bool("headless", env.HEADLESS) if headless is None else headless
        self.browser = playwright.chromium.launch(headless=headless)
        self.context = self.browser.new_context(
            viewport={"width": 1280, "height": 900})
        self.page = self.context.new_page()
        self.http_errors = []
        self.js_errors = []
        self._attach_monitors()

    def _attach_monitors(self):
        page = self.page

        def on_response(resp):
            if resp.status in CRITICAL_HTTP:
                req = resp.request
                self.http_errors.append({
                    "url": req.url,
                    "status": resp.status,
                    "resource_type": req.resource_type,
                })

        def on_pageerror(err):
            self.js_errors.append({"type": "pageerror", "message": str(err)})

        def on_console(msg):
            if msg.type == "error":
                self.js_errors.append({"type": "console.error", "message": msg.text})

        page.on("response", on_response)
        page.on("pageerror", on_pageerror)
        page.on("console", on_console)

    def goto(self, url, wait="domcontentloaded", timeout=None):
        t = timeout or setting_int("page_timeout", env.PAGE_TIMEOUT)
        try:
            return self.page.goto(url, wait_until=wait, timeout=t)
        except Exception as e:
            msg = str(e).lower()
            if "net::err" in msg or "network" in msg or "timeout" in msg:
                try:
                    return self.page.goto(url, wait_until="commit",
                                          timeout=min(t * 2, 90000))
                except Exception:
                    pass
            raise

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass

    def drain_http_errors(self):
        errs, self.http_errors = self.http_errors, []
        return errs

    def drain_js_errors(self):
        errs, self.js_errors = self.js_errors, []
        return errs


def open_session(session_factory=None, headless=None):
    from playwright.sync_api import sync_playwright
    pw = session_factory() if session_factory else sync_playwright().start()
    return BrowserSession(pw, headless=headless), (pw.stop if not session_factory else None)


def has_login_form(page):
    try:
        return len(page.query_selector_all(LOGIN_SELECTORS["password"])) > 0
    except Exception:
        return False


def try_login(page, email, password):
    """Fill and submit login form if present. Returns True if no blocker."""
    try:
        pw = page.query_selector_all(LOGIN_SELECTORS["password"])
        if not pw:
            return True
        em = page.query_selector(LOGIN_SELECTORS["email"])
        if em:
            em.fill(email or "")
        pw[0].fill(password or "")
        sub = page.query_selector(LOGIN_SELECTORS["submit"])
        if sub:
            sub.click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        return True
    except Exception:
        return False


def settle(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    try:
        page.wait_for_timeout(800)
    except Exception:
        pass


def find_otp_field(page):
    try:
        return page.query_selector(LOGIN_SELECTORS["otp"])
    except Exception:
        return None


def fill_otp_code(page, code):
    """Fill the OTP code and submit the form. Returns True if the OTP field
    was found and the code was submitted."""
    otp = find_otp_field(page)
    if not otp:
        return False
    try:
        otp.fill(code)
    except Exception:
        return False
    sub = page.query_selector(LOGIN_SELECTORS["submit"])
    if sub:
        try:
            sub.click()
        except Exception:
            pass
    return True


def authenticate(page, project):
    """Authentifier la session en UNE SEULE étape : login (simple ou 2FA)
    puis récupération automatique du code OTP si l'écran l'exige.

    - auth_type "none" : rien à faire, retourne True.
    - auth_type "simple" : remplit + soumet le formulaire de connexion.
    - auth_type "2fa" : login puis, si un champ OTP apparaît, lecture du code
      dans la boîte mail (IMAP) et saisie automatique.

    Retourne True quand la session est authentifiée et exploitable.
    Retourne False uniquement si un code OTP était requis mais n'a pas pu être
    récupéré automatiquement (l'appelant décide : saisie manuelle via le
    moteur, ou erreur claire pour le scanner). Ne ferme jamais la session.
    """
    from .. import security
    if str(project.get("auth_type", "")).lower() == "none":
        return True
    # Prendre un snapshot de la boîte mail AVANT le login.
    # Le login déclenche l'envoi du code OTP ; on ne voudra que
    # les mails reçus après ce snapshot.
    from . import mailbox
    recipient = (project.get("email") or "").strip()
    snapshot = mailbox.snapshot_latest_id(recipient=recipient)
    if has_login_form(page):
        try_login(page, project.get("email", ""),
                  security.decrypt_value(project.get("password_enc", "")))
        settle(page)
    if str(project.get("auth_type", "")).lower() != "2fa":
        return True
    if find_otp_field(page) is None:
        return True
    if not setting_bool("otp_auto_fetch", True):
        return False
    try:
        code = mailbox.fetch_otp_code(recipient=recipient, since_id=snapshot)
    except Exception:
        code = None
    if code and fill_otp_code(page, code):
        settle(page)
        return True
    return False
