import os
import json
import time
import hashlib
import sqlite3
import threading
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g
)
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY", "change-me-in-production")

DATABASE = os.getenv("DATABASE_PATH", "qa.db")
SCREENSHOT_DIR = os.path.join("static", "screenshots")

# Limites configurables (voir .env)
MAX_PAGES = int(os.getenv("MAX_PAGES", "40"))
MAX_FORMS_PER_PAGE = int(os.getenv("MAX_FORMS_PER_PAGE", "5"))
MAX_BUTTONS_PER_PAGE = int(os.getenv("MAX_BUTTONS_PER_PAGE", "8"))

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        email TEXT NOT NULL,
        password_enc TEXT NOT NULL,
        auth_type TEXT NOT NULL DEFAULT 'simple',
        comments TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        created_by TEXT DEFAULT '',
        updated_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS project_pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        last_status INTEGER DEFAULT 0,
        discovered_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS page_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        page_url TEXT NOT NULL,
        page_name TEXT NOT NULL,
        action_type TEXT NOT NULL,
        label TEXT DEFAULT '',
        el_index INTEGER DEFAULT 0,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        started_at TEXT DEFAULT (datetime('now')),
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'running',
        total INTEGER DEFAULT 0,
        passed INTEGER DEFAULT 0,
        failed INTEGER DEFAULT 0,
        warning INTEGER DEFAULT 0,
        skipped INTEGER DEFAULT 0,
        report_json TEXT DEFAULT '{}',
        otp_code TEXT DEFAULT '',
        selected_modules TEXT DEFAULT '',
        created_by TEXT DEFAULT '',
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id INTEGER NOT NULL,
        module TEXT NOT NULL,
        function_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PASS',
        severity TEXT DEFAULT '',
        expected TEXT DEFAULT '',
        obtained TEXT DEFAULT '',
        screenshot TEXT DEFAULT '',
        http_status INTEGER DEFAULT 0,
        FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
    );
    """)
    db.commit()

    # Create admin user if none exists
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    cur = db.execute("SELECT id FROM users LIMIT 1")
    if cur.fetchone() is None:
        pw_hash = hashlib.sha256(admin_password.encode()).hexdigest()
        db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)",
                   (admin_email, pw_hash))
        db.commit()

    # Migration: add otp_code column if missing
    try:
        db.execute("SELECT otp_code FROM tests LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE tests ADD COLUMN otp_code TEXT DEFAULT ''")
        db.commit()

    # Migration: add selected_modules column if missing
    try:
        db.execute("SELECT selected_modules FROM tests LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE tests ADD COLUMN selected_modules TEXT DEFAULT ''")
        db.commit()

    # Migration: add http_status column if missing
    try:
        db.execute("SELECT http_status FROM results LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE results ADD COLUMN http_status INTEGER DEFAULT 0")
        db.commit()

    # Migration: add last_status column if missing
    try:
        db.execute("SELECT last_status FROM project_pages LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE project_pages ADD COLUMN last_status INTEGER DEFAULT 0")
        db.commit()

    # Migration: track who created/modified projects and who launched tests
    for table, column, ddl in [
        ("projects", "created_by", "TEXT DEFAULT ''"),
        ("projects", "updated_by", "TEXT DEFAULT ''"),
        ("tests", "created_by", "TEXT DEFAULT ''"),
        ("tests", "selected_actions", "TEXT DEFAULT ''"),
    ]:
        try:
            db.execute(f"SELECT {column} FROM {table} LIMIT 1")
        except sqlite3.OperationalError:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            db.commit()

    db.close()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        db = get_db()
        user = db.execute("SELECT email FROM users WHERE id=?", (session["user_id"],)).fetchone()
        admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
        if not user or user["email"] != admin_email:
            flash("Acces reserve aux administrateurs", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Simple encryption for project passwords (Fernet-like XOR for V1)
# For production, use proper Fernet / AES.
# ---------------------------------------------------------------------------

import base64 as _base64

_APP_KEY = (os.getenv("APP_SECRET_KEY", "change-me") * 32)[:32]


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def encrypt_value(plain: str) -> str:
    return _base64.b64encode(_xor_bytes(plain.encode(), _APP_KEY.encode())).decode()


def decrypt_value(cipher: str) -> str:
    return _xor_bytes(_base64.b64decode(cipher), _APP_KEY.encode()).decode()


# ---------------------------------------------------------------------------
# OpenAI integration stub
# ---------------------------------------------------------------------------

_openai_client = None


def get_openai():
    global _openai_client
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


QA_SYSTEM_PROMPT = """You are a Senior QA Analyst, SDET Senior, expert in functional and regression testing.

You are controlling a web browser via Playwright to test a web application.

For each action you perform, you must follow this mandatory process:
1. Define the action
2. Determine the expected result
3. Observe the actual result
4. Compare expected vs actual
5. Mark as PASS, FAIL, WARNING, or SKIPPED

NEVER consider "I clicked Save" as PASS. You must verify the actual outcome.

Available statuses: PASS, FAIL, WARNING, SKIPPED

Respond with JSON in the format:
{
  "action": "description of what to do",
  "result_expected": "what should happen",
  "result_obtained": "what actually happened (fill after execution)",
  "status": "PASS|FAIL|WARNING|SKIPPED",
  "module": "module name",
  "function": "function tested",
  "severity": "CRITICAL|MAJOR|MINOR|INFO (if FAIL)",
  "continue": true/false,
  "reasoning": "brief explanation"
}
"""


# ---------------------------------------------------------------------------
# QA Test Engine
# ---------------------------------------------------------------------------

LOGOUT_HINTS = ("logout", "log-out", "log_out", "signout", "sign-out",
                "deconnexion", "déconnexion", "deconnecter", "se-deconnecter",
                "quitter")

FILE_EXT_SKIP = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf",
                 ".zip", ".rar", ".css", ".js", ".json", ".xml", ".woff",
                 ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".webp")


def _is_cancelled(db, test_id):
    row = db.execute("SELECT status FROM tests WHERE id=?", (test_id,)).fetchone()
    return bool(row) and row["status"] == "cancel_requested"


def _zero_counters():
    return {"total": 0, "passed": 0, "failed": 0, "warning": 0, "skipped": 0}


def _record_result(db, test_id, all_results, counters, result_data, screenshot_path="", http_status=0):
    status = result_data["status"]
    counters["total"] += 1
    if status == "PASS":
        counters["passed"] += 1
    elif status == "FAIL":
        counters["failed"] += 1
    elif status == "WARNING":
        counters["warning"] += 1
    else:
        counters["skipped"] += 1
    all_results.append(result_data)
    db.execute(
        """INSERT INTO results (test_id, module, function_name, status, severity, expected, obtained, screenshot, http_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (test_id, result_data["module"], result_data["function"], status,
         result_data["severity"], result_data["expected"],
         result_data["obtained"], screenshot_path, http_status)
    )
    db.execute(
        "UPDATE tests SET total=?, passed=?, failed=?, warning=?, skipped=? WHERE id=?",
        (counters["total"], counters["passed"], counters["failed"],
         counters["warning"], counters["skipped"], test_id)
    )
    db.commit()


def _finalize_test(db, test_id, status, all_results, counters):
    db.execute(
        """UPDATE tests SET status=?, finished_at=datetime('now'),
           total=?, passed=?, failed=?, warning=?, skipped=?, report_json=? WHERE id=?""",
        (status, counters["total"], counters["passed"], counters["failed"],
         counters["warning"], counters["skipped"],
         json.dumps(all_results, ensure_ascii=False), test_id)
    )
    db.commit()


def _save_test_error(db, test_id, message):
    db.execute(
        "UPDATE tests SET status='failed', finished_at=datetime('now'), report_json=? WHERE id=?",
        (json.dumps({"error": message}), test_id)
    )
    db.commit()


def _try_login(page, email, password):
    """Fill and submit a login form if one is visible. Returns True if no blocker."""
    try:
        pw_inputs = page.query_selector_all('input[type="password"]')
        if not pw_inputs:
            return True  # no login form -> assume already authenticated
        email_inputs = page.query_selector_all(
            'input[type="email"], input[name*="email"], input[placeholder*="mail"]')
        if email_inputs:
            email_inputs[0].fill(email)
        pw_inputs[0].fill(password)
        submit = page.query_selector('button[type="submit"], input[type="submit"]')
        if submit:
            submit.click()
            page.wait_for_timeout(3000)
        return True
    except Exception:
        return False


def _safe_click(page, target_text):
    try:
        page.click(f"text={target_text}", timeout=4000)
        page.wait_for_timeout(1500)
        return True
    except Exception:
        return False


def _save_screenshot(test_id, idx, screenshot_bytes):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    rel_path = f"screenshots/test_{test_id}_{idx}.png"
    filepath = os.path.join("static", rel_path)
    try:
        with open(filepath, "wb") as f:
            f.write(screenshot_bytes)
        return "/" + rel_path.replace("\\", "/")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Exploration phase: crawl all pages/links of the application
# ---------------------------------------------------------------------------

def run_exploration(test_id, project_id):
    """Crawl the application and discover all reachable pages (modules)."""
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlparse

    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row

    project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        db.close()
        return

    start_url = project["url"]
    email = project["email"]
    password = decrypt_value(project["password_enc"])

    base_netloc = urlparse(start_url).netloc
    found = {}          # url -> {"name": str, "status": int}
    all_actions = []    # [{page_url, page_name, action_type, label, el_index}]
    to_visit = [start_url]
    visited = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            page.goto(start_url, timeout=30000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1000)
            _try_login(page, email, password)

            # Handle OTP wait during exploration too
            if project["auth_type"] == "2fa":
                otp_field = page.query_selector('input[name*="otp"], input[name*="code"], input[placeholder*="code"]')
                if otp_field:
                    db.execute("UPDATE tests SET status='waiting_otp' WHERE id=?", (test_id,))
                    db.commit()
                    while True:
                        time.sleep(2)
                        row = db.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
                        if row["status"] == "otp_submitted":
                            code = row["otp_code"]
                            try:
                                otp_field = page.query_selector('input[name*="otp"], input[name*="code"], input[placeholder*="code"]')
                                if otp_field:
                                    otp_field.fill(code)
                                confirm = page.query_selector('button[type="submit"]')
                                if confirm:
                                    confirm.click()
                                page.wait_for_timeout(3000)
                            except Exception:
                                pass
                            break
                        elif row["status"] == "cancel_requested":
                            browser.close()
                            _finalize_test(db, test_id, "cancelled", [], _zero_counters())
                            db.close()
                            return
        except Exception as e:
            _save_test_error(db, test_id, f"Exploration - impossible de charger l'URL: {e}")
            browser.close()
            db.close()
            return

        while to_visit and len(visited) < MAX_PAGES:
            if _is_cancelled(db, test_id):
                browser.close()
                _finalize_test(db, test_id, "cancelled", [], _zero_counters())
                db.close()
                return

            current = to_visit.pop(0)
            if current in visited:
                continue
            try:
                resp = page.goto(current, timeout=20000)
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(600)
                http_status = resp.status if resp else 0
            except Exception:
                visited.add(current)
                continue

            visited.add(current)

            name = ""
            h1 = page.query_selector("h1")
            if h1:
                name = h1.inner_text().strip()[:60]
            if not name:
                name = (page.title() or "").strip()[:60]
            if not name:
                name = urlparse(current).path or "Accueil"
            found[current] = {"name": name[:60], "status": http_status}

            # Full load + scroll through the page so that ALL buttons
            # (including lazy-rendered ones) are present before scanning.
            _settle_page(page)
            _scroll_full_page(page)

            # Inventory the interactive elements of this page
            for act in _scan_page_actions(page):
                all_actions.append({
                    "page_url": current,
                    "page_name": name[:60],
                    "action_type": act["action_type"],
                    "label": act["label"],
                    "el_index": act["el_index"],
                })

            # Live progress: number of pages discovered so far
            db.execute("UPDATE tests SET total=? WHERE id=?", (len(found), test_id))
            db.commit()

            try:
                links = page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => ({href: e.href, text: (e.innerText || '').trim()}))"
                )
            except Exception:
                links = []

            for l in links:
                href = l.get("href") or ""
                text = (l.get("text") or "").lower()
                if not href.startswith("http"):
                    continue
                parsed = urlparse(href)
                if parsed.netloc != base_netloc:
                    continue
                if href.split("?")[0].lower().endswith(FILE_EXT_SKIP):
                    continue
                if any(hint in href.lower() for hint in LOGOUT_HINTS):
                    continue
                if any(hint in text for hint in LOGOUT_HINTS):
                    continue
                if href.rstrip("/") == start_url.rstrip("/"):
                    continue
                if href in visited or href in found or href in to_visit:
                    continue
                to_visit.append(href)

        browser.close()

    if _is_cancelled(db, test_id):
        _finalize_test(db, test_id, "cancelled", [], _zero_counters())
        db.close()
        return

    # Persist discovered pages for this project
    db.execute("DELETE FROM project_pages WHERE project_id=?", (project_id,))
    for u, info in found.items():
        db.execute(
            "INSERT INTO project_pages (project_id, name, url, last_status) VALUES (?, ?, ?, ?)",
            (project_id, info["name"], u, info["status"])
        )

    # Persist the inventoried actions (buttons/forms/searches) per page
    db.execute("DELETE FROM page_actions WHERE project_id=?", (project_id,))
    for act in all_actions:
        db.execute(
            """INSERT INTO page_actions (project_id, page_url, page_name, action_type, label, el_index)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, act["page_url"], act["page_name"],
             act["action_type"], act["label"], act["el_index"])
        )
    db.execute(
        "UPDATE tests SET status='explored', finished_at=datetime('now'), total=? WHERE id=?",
        (len(found), test_id)
    )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# QA interaction helpers: real actions on forms, buttons and searches
# ---------------------------------------------------------------------------

DANGEROUS_HINTS = ("delete", "supprim", "remove", "logout", "log-out", "deconnexion",
                   "déconnexion", "deconnect", "signout", "sign-out", "quitter",
                   "drop", "trash", "pay", "paiement", "payer", "checkout")

SUCCESS_HINTS = ("succes", "success", "créé", "cree", "enregistré", "enregistre",
                 "ajouté", "ajoute", "modifié", "modifie", "bienvenue", "merci",
                 "confirmé", "confirme", "création réussie")

ERROR_HINTS = ("erreur", "error", "invalide", "invalid", "obligatoire", "required",
               "requis", "échoué", "echoue", "failed", "incorrect", "champs")


def _qa_fill_value(input_type, name):
    """Generate realistic QA data based on input type/name."""
    input_type = (input_type or "").lower()
    name_l = (name or "").lower()
    if input_type == "email" or "mail" in name_l:
        return "qa.test@example.com"
    if input_type == "password":
        return "QaTest123!"
    if input_type == "number":
        return "42"
    if input_type == "tel" or "tel" in name_l or "phone" in name_l:
        return "0600000000"
    if input_type == "url":
        return "https://example.com"
    if "date" in name_l:
        return "2026-01-15"
    if "name" in name_l or "nom" in name_l or "titre" in name_l or "title" in name_l:
        return "QA Test Automatique"
    if "search" in name_l or "recherch" in name_l or "query" in name_l:
        return "test"
    return "Test QA automatique"


def _page_snapshot(page):
    """Capture URL + visible text for before/after comparison."""
    try:
        text = page.evaluate("document.body.innerText").strip()[:2000].lower()
    except Exception:
        text = ""
    return {"url": page.url, "text": text}


def _heuristic_verdict(before, after):
    """Compare page state before/after an action -> (status, obtained)."""
    url_changed = after["url"] != before["url"]
    has_success = any(h in after["text"] for h in SUCCESS_HINTS)
    had_success = any(h in before["text"] for h in SUCCESS_HINTS)
    has_error = any(h in after["text"] for h in ERROR_HINTS)
    had_error = any(h in before["text"] for h in ERROR_HINTS)

    if has_error and not had_error:
        return ("PASS", "Un message de validation est apparu (champs obligatoires / erreur affichee)")
    if has_success and not had_success:
        return ("PASS", "Un message de succes est apparu")
    if url_changed:
        return ("PASS", f"Navigation effectuee vers {after['url']}")
    if after["text"] != before["text"]:
        return ("WARNING", "Le contenu de la page a change sans message clair")
    return ("FAIL", "Aucun changement visible apres l'action")


def _ai_verdict(client, mod_name, action_desc, before, after):
    """Ask OpenAI to evaluate the outcome of one performed action."""
    user_msg = f"""Module teste: {mod_name}
Action realisee: {action_desc}

URL avant: {before['url']}
Contenu avant (extrait): {before['text'][:600]}

URL apres: {after['url']}
Contenu apres (extrait): {after['text'][:900]}

Evalue le resultat de cette action: le comportement est-il correct ?
Reponds avec un JSON valide uniquement:
{{"status": "PASS|FAIL|WARNING", "result_expected": "...", "result_obtained": "...", "severity": "CRITICAL|MAJOR|MINOR|"}}"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": QA_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg}
            ],
            max_tokens=300,
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)
    except Exception:
        status, obtained = _heuristic_verdict(before, after)
        return {"status": status, "result_obtained": obtained,
                "result_expected": "Comportement correct apres l'action", "severity": ""}


def _goto_module(page, mod_url):
    """Navigate to a module page. Returns the HTTP status code (0 if unknown)."""
    if not mod_url:
        return 0
    try:
        resp = page.goto(mod_url, timeout=20000)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(800)
        return resp.status if resp else 0
    except Exception:
        return 0


def _is_dangerous(label):
    label = (label or "").lower()
    return any(h in label for h in DANGEROUS_HINTS)


ADD_HINTS = ("ajouter", "ajout", "nouveau", "nouvelle", "new", "add",
             "créer", "creer", "create", "+")

# Click destructive buttons (delete/trash icons...) during QA tests.
# Can be disabled in .env: TEST_DANGEROUS_ACTIONS=false
TEST_DANGEROUS_ACTIONS = os.getenv("TEST_DANGEROUS_ACTIONS", "true").lower() == "true"


def _element_label(el, fallback=""):
    """Best-effort readable label for a button/link (text, value, aria-label, title)."""
    try:
        label = (el.inner_text() or "").strip()
    except Exception:
        label = ""
    if not label:
        label = (el.get_attribute("value") or "").strip()
    if not label:
        label = (el.get_attribute("aria-label") or "").strip()
    if not label:
        label = (el.get_attribute("title") or "").strip()
    return label or fallback


def _fill_container_fields(container):
    """Fill every fillable input inside a container. Returns number of fields filled."""
    filled = 0
    for inp in container.query_selector_all("input, select, textarea"):
        i_type = (inp.get_attribute("type") or "text").lower()
        i_name = inp.get_attribute("name") or ""
        if i_type in ("hidden", "submit", "button", "file"):
            continue
        try:
            if inp.tag_name().lower() == "select":
                opts = inp.query_selector_all("option")
                if len(opts) > 1:
                    inp.select_option(index=1)
                    filled += 1
            elif i_type in ("checkbox", "radio"):
                if not inp.is_checked():
                    inp.check()
                    filled += 1
            else:
                inp.fill(_qa_fill_value(i_type, i_name))
                filled += 1
        except Exception:
            continue
    return filled


def _submit_container(page, container):
    submit = container.query_selector('button[type="submit"], input[type="submit"], button:not([type])')
    if submit:
        submit.click()
    else:
        page.keyboard.press("Enter")
    page.wait_for_timeout(2000)
    try:
        page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass


def _visible_form_or_modal(page):
    """Find a visible form/modal that just appeared (e.g. after clicking 'Ajouter')."""
    candidates = page.query_selector_all(
        '[role="dialog"] form, .modal form, form, [role="dialog"], .modal'
    )
    for el in candidates:
        try:
            if el.is_visible():
                tag = el.tag_name().lower()
                if tag == "form":
                    return el
                form = el.query_selector("form")
                if form:
                    return form
        except Exception:
            continue
    return None


def _record_action(db, test_id, all_results, counters, client, mod_name,
                   function_label, action_desc, before, after,
                   expected_default, screenshot_path):
    """Evaluate one performed action (AI if available, heuristics otherwise) and store it."""
    if client:
        v = _ai_verdict(client, mod_name, action_desc, before, after)
        status_v = v.get("status", "WARNING").upper()
        expected = v.get("result_expected", "") or expected_default
        obtained = v.get("result_obtained", "")
        severity = v.get("severity", "")
    else:
        status_v, obtained = _heuristic_verdict(before, after)
        expected = expected_default
        severity = "MAJOR" if status_v == "FAIL" else ""

    _record_result(db, test_id, all_results, counters, {
        "module": mod_name,
        "function": function_label,
        "status": status_v,
        "severity": severity,
        "expected": expected,
        "obtained": obtained,
    }, screenshot_path)


def _shot(page, test_id, shot_idx_ref):
    path = _save_screenshot(test_id, shot_idx_ref[0] + 1, page.screenshot())
    shot_idx_ref[0] += 1
    return path


BTN_SCAN_SELECTOR = ('button, [role="button"], input[type="submit"], '
                     'input[type="button"], input[type="reset"]')
SEARCH_SCAN_SELECTOR = ('input[type="search"], input[name*="search" i], '
                        'input[name*="query" i], input[name*="recherch" i], '
                        'input[placeholder*="recherch" i], input[placeholder*="search" i]')


def _settle_page(page):
    """Wait until the page is fully loaded (network idle + margin)."""
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass
    try:
        page.wait_for_timeout(800)
    except Exception:
        pass


def _scroll_full_page(page):
    """Scroll through the whole page to trigger lazy-loaded content,
    then return to the top."""
    try:
        page.evaluate("""async () => {
            await new Promise(resolve => {
                let total = 0;
                const step = () => {
                    window.scrollBy(0, 700);
                    total += 700;
                    const max = document.body.scrollHeight;
                    if (total >= max || total > 30000) {
                        window.scrollTo(0, 0);
                        resolve();
                    } else {
                        setTimeout(step, 100);
                    }
                };
                step();
            });
        }""")
        page.wait_for_timeout(500)
    except Exception:
        pass


def _scan_page_actions(page):
    """Inventory ALL interactive elements of the current page.

    Every <button> tag is taken into account, whatever its type
    (button/submit/reset), its position or its visibility (hidden buttons,
    menu buttons, modal buttons... are included). Scrolling must be done
    BEFORE calling this function so lazily rendered buttons exist in the DOM.
    """
    actions = []

    # ALL buttons: <button> (tous types), [role=button], inputs submit/button/reset.
    # No visibility filter: hidden buttons are inventoried too.
    try:
        buttons = page.query_selector_all(BTN_SCAN_SELECTOR)
        for idx, btn in enumerate(buttons):
            try:
                label = _element_label(btn)
                # Only exclude logout links/buttons (would kill the session)
                if label and any(h in label.lower() for h in LOGOUT_HINTS):
                    continue
                if not label:
                    label = f"Bouton #{idx + 1}"
                actions.append({
                    "action_type": "button",
                    "label": label[:60],
                    "el_index": idx,
                })
            except Exception:
                continue
    except Exception:
        pass

    # Forms
    try:
        forms = page.query_selector_all("form")
        for idx, form in enumerate(forms):
            actions.append({
                "action_type": "form",
                "label": f"Formulaire #{idx + 1}",
                "el_index": idx,
            })
    except Exception:
        pass

    # Search field
    try:
        if page.query_selector(SEARCH_SCAN_SELECTOR):
            actions.append({
                "action_type": "search",
                "label": "Recherche",
                "el_index": 0,
            })
    except Exception:
        pass

    return actions


def _perform_action(page, db, test_id, all_results, counters, client,
                    act, shot_idx_ref):
    """Execute exactly ONE selected action and record its result.

    act: {action_type: button|form|search, label, el_index, page_name, page_url}
    """
    mod_name = act.get("page_name") or "General"
    a_type = act.get("action_type")
    label = act.get("label") or a_type
    idx = int(act.get("el_index") or 0)

    try:
        if a_type == "button":
            els = page.query_selector_all(BTN_SCAN_SELECTOR)
            if idx >= len(els):
                _record_result(db, test_id, all_results, counters, {
                    "module": mod_name, "function": f"Bouton '{label}'",
                    "status": "SKIPPED", "severity": "",
                    "expected": "Le bouton est present sur la page",
                    "obtained": f"Bouton introuvable (index {idx}, {len(els)} bouton(s) detectes)",
                })
                return
            btn = els[idx]

            before = _page_snapshot(page)
            screenshot_path = _shot(page, test_id, shot_idx_ref)

            # Attempt the click even if the button is hidden/disabled:
            # Playwright scrolls to it and waits for it to be actionable.
            try:
                btn.click(timeout=6000)
            except Exception:
                _record_result(db, test_id, all_results, counters, {
                    "module": mod_name, "function": f"Bouton '{label}'",
                    "status": "WARNING", "severity": "MINOR",
                    "expected": "Le bouton est cliquable",
                    "obtained": "Bouton present mais non cliquable (masque ou desactive)",
                }, screenshot_path)
                return

            page.wait_for_timeout(1500)
            try:
                page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass

            # If clicking opened a form/modal (e.g. 'Ajouter'), fill + submit it
            modal_form = _visible_form_or_modal(page)
            if modal_form:
                filled = _fill_container_fields(modal_form)
                if filled > 0:
                    before_submit = _page_snapshot(page)
                    submit_shot = _shot(page, test_id, shot_idx_ref)
                    _submit_container(page, modal_form)
                    after_submit = _page_snapshot(page)
                    _record_action(
                        db, test_id, all_results, counters, client, mod_name,
                        f"'{label}' + formulaire",
                        f"Cliquer '{label}' puis remplir le formulaire ouvert ({filled} champ(s)) et soumettre",
                        before_submit, after_submit,
                        "L'element est cree / traite et un retour visible s'affiche",
                        submit_shot)
                    return

            after = _page_snapshot(page)
            _record_action(
                db, test_id, all_results, counters, client, mod_name,
                f"Bouton '{label}'", f"Cliquer sur le bouton '{label}'",
                before, after,
                f"Le bouton '{label}' produit une reaction visible",
                screenshot_path)

        elif a_type == "form":
            forms = page.query_selector_all("form")
            if idx >= len(forms):
                _record_result(db, test_id, all_results, counters, {
                    "module": mod_name, "function": label,
                    "status": "SKIPPED", "severity": "",
                    "expected": "Le formulaire est present sur la page",
                    "obtained": f"Formulaire introuvable (index {idx})",
                })
                return
            form = forms[idx]
            filled = _fill_container_fields(form)
            if filled == 0:
                _record_result(db, test_id, all_results, counters, {
                    "module": mod_name, "function": label,
                    "status": "SKIPPED", "severity": "",
                    "expected": "Le formulaire contient des champs remplissables",
                    "obtained": "Aucun champ remplissable trouve",
                })
                return

            before = _page_snapshot(page)
            screenshot_path = _shot(page, test_id, shot_idx_ref)
            _submit_container(page, form)
            after = _page_snapshot(page)
            _record_action(
                db, test_id, all_results, counters, client, mod_name,
                label, f"Remplir le formulaire ({filled} champ(s)) et soumettre",
                before, after,
                "Le formulaire traite la soumission (message de succes ou de validation)",
                screenshot_path)

        elif a_type == "search":
            s_input = page.query_selector(SEARCH_SCAN_SELECTOR)
            if not s_input:
                _record_result(db, test_id, all_results, counters, {
                    "module": mod_name, "function": "Recherche",
                    "status": "SKIPPED", "severity": "",
                    "expected": "Le champ de recherche est present",
                    "obtained": "Champ de recherche introuvable",
                })
                return

            before = _page_snapshot(page)
            screenshot_path = _shot(page, test_id, shot_idx_ref)
            s_input.fill("test")
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)
            try:
                page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass
            after = _page_snapshot(page)
            _record_action(
                db, test_id, all_results, counters, client, mod_name,
                "Recherche", "Effectuer une recherche avec le mot 'test'",
                before, after,
                "La recherche renvoie des resultats ou un message 'aucun resultat'",
                screenshot_path)
    except Exception as e:
        _record_result(db, test_id, all_results, counters, {
            "module": mod_name, "function": label,
            "status": "FAIL", "severity": "MINOR",
            "expected": "L'action s'execute sans erreur",
            "obtained": f"Erreur pendant l'execution: {e}",
        })


# ---------------------------------------------------------------------------
# QA Test phase: test selected modules with OpenAI
# ---------------------------------------------------------------------------

def run_qa_test(test_id, project_id):
    """Run a QA test session on the selected modules (background thread)."""
    from playwright.sync_api import sync_playwright

    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row

    project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    test_row = db.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
    if not project or not test_row:
        db.close()
        return

    url = project["url"]
    email = project["email"]
    password = decrypt_value(project["password_enc"])
    auth_type = project["auth_type"]
    comments = project["comments"] or ""

    try:
        selected_modules = json.loads(test_row["selected_modules"]) if test_row["selected_modules"] else []
    except Exception:
        selected_modules = []
    if not selected_modules:
        selected_modules = [{"name": "Application complete", "url": url}]

    all_results = []
    counters = _zero_counters()
    client = get_openai()
    shot_idx = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            page.goto(url, timeout=30000)
            page.wait_for_load_state("domcontentloaded")
        except Exception as e:
            _save_test_error(db, test_id, f"Failed to load URL: {e}")
            browser.close()
            db.close()
            return

        # --- Login phase ---
        if auth_type != "none":
            if not _try_login(page, email, password):
                _save_test_error(db, test_id, "Login failed")
                browser.close()
                db.close()
                return

            if auth_type == "2fa":
                otp_field = page.query_selector('input[name*="otp"], input[name*="code"], input[placeholder*="code"]')
                if otp_field:
                    db.execute("UPDATE tests SET status='waiting_otp' WHERE id=?", (test_id,))
                    db.commit()

                    while True:
                        time.sleep(2)
                        row = db.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
                        if row["status"] == "otp_submitted":
                            otp_code = row["otp_code"]
                            try:
                                otp_field = page.query_selector('input[name*="otp"], input[name*="code"], input[placeholder*="code"]')
                                if otp_field:
                                    otp_field.fill(otp_code)
                                confirm = page.query_selector('button[type="submit"], button:has-text("Valider"), button:has-text("Continue")')
                                if confirm:
                                    confirm.click()
                                page.wait_for_timeout(3000)
                            except Exception as e:
                                _save_test_error(db, test_id, f"OTP validation failed: {e}")
                                browser.close()
                                db.close()
                                return
                            break
                        elif row["status"] == "cancel_requested":
                            browser.close()
                            _finalize_test(db, test_id, "cancelled", [], counters)
                            db.close()
                            return

        # --- QA Testing phase: execute ONLY the user-selected actions ---
        page.on("dialog", lambda d: d.accept())
        shot_idx_ref = [shot_idx]

        try:
            selected_actions = json.loads(test_row["selected_actions"]) if test_row["selected_actions"] else []
        except Exception:
            selected_actions = []

        # Group actions by page (order preserved): one page load per page,
        # then each selected action runs once, in the discovered order.
        pages_order = []
        actions_by_page = {}
        for act in selected_actions:
            purl = act.get("page_url") or url
            pname = act.get("page_name") or "General"
            if purl not in actions_by_page:
                actions_by_page[purl] = {"name": pname, "actions": []}
                pages_order.append(purl)
            actions_by_page[purl]["actions"].append(act)

        for purl in pages_order:
            if _is_cancelled(db, test_id):
                break

            group = actions_by_page[purl]
            mod_name = group["name"]

            # Single load of this page (same settle+scroll sequence as the
            # exploration scan so button indexes match exactly)
            http_status = _goto_module(page, purl)
            _settle_page(page)
            _scroll_full_page(page)
            if http_status:
                db.execute(
                    "UPDATE project_pages SET last_status=? WHERE project_id=? AND url=?",
                    (http_status, project_id, purl)
                )
                db.commit()

            # Access result with HTTP status code (once per page)
            if http_status and http_status < 400:
                access_status, access_sev = "PASS", ""
            elif http_status >= 400:
                access_status, access_sev = "FAIL", "CRITICAL" if http_status >= 500 else "MAJOR"
            else:
                access_status, access_sev = "WARNING", ""

            screenshot_path = _save_screenshot(test_id, shot_idx_ref[0] + 1, page.screenshot())
            shot_idx_ref[0] += 1
            _record_result(db, test_id, all_results, counters, {
                "module": mod_name,
                "function": "Acces au module",
                "status": access_status,
                "severity": access_sev,
                "expected": "La page du module repond avec un code 2xx/3xx",
                "obtained": f"Code HTTP {http_status or 'inconnu'} - {page.url}",
            }, screenshot_path, http_status)

            # Execute each selected action ONCE, in order.
            # Reload only if a previous action navigated away from the page.
            for act in group["actions"]:
                if _is_cancelled(db, test_id):
                    break
                if page.url != purl:
                    _goto_module(page, purl)
                    _settle_page(page)
                _perform_action(page, db, test_id, all_results, counters,
                                client, act, shot_idx_ref)

        shot_idx = shot_idx_ref[0]
        browser.close()

    if _is_cancelled(db, test_id):
        _finalize_test(db, test_id, "cancelled", all_results, counters)
    else:
        _finalize_test(db, test_id, "completed", all_results, counters)
    db.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE email=? AND password_hash=?",
            (email, hash_password(password))
        ).fetchone()
        if user:
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            return redirect(url_for("dashboard"))
        flash("Identifiants incorrects", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---- Dashboard ----

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    total_projects = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    active_projects = db.execute("SELECT COUNT(*) FROM projects WHERE status='active'").fetchone()[0]
    total_tests = db.execute("SELECT COUNT(*) FROM tests WHERE status='completed'").fetchone()[0]
    total_passed = db.execute("SELECT COALESCE(SUM(passed),0) FROM tests WHERE status='completed'").fetchone()[0]
    total_failed = db.execute("SELECT COALESCE(SUM(failed),0) FROM tests WHERE status='completed'").fetchone()[0]
    total_all = total_passed + total_failed + \
        db.execute("SELECT COALESCE(SUM(warning),0) FROM tests WHERE status='completed'").fetchone()[0]

    success_rate = round((total_passed / total_all) * 100) if total_all > 0 else 0
    fail_rate = 100 - success_rate

    total_warning = db.execute("SELECT COALESCE(SUM(warning),0) FROM tests WHERE status='completed'").fetchone()[0]
    total_skipped = db.execute("SELECT COALESCE(SUM(skipped),0) FROM tests WHERE status='completed'").fetchone()[0]

    last_test = db.execute("""
        SELECT t.*, p.name as project_name FROM tests t
        JOIN projects p ON t.project_id = p.id
        WHERE t.status IN ('completed','failed')
        ORDER BY t.finished_at DESC LIMIT 1
    """).fetchone()

    recent_tests = db.execute("""
        SELECT t.*, p.name as project_name FROM tests t
        JOIN projects p ON t.project_id = p.id
        ORDER BY t.started_at DESC LIMIT 10
    """).fetchall()

    # Chart: status distribution
    status_dist = {
        "PASS": total_passed,
        "FAIL": total_failed,
        "WARNING": total_warning,
        "SKIPPED": total_skipped,
    }

    # Chart: success rate trend over the last completed tests
    trend_rows = db.execute("""
        SELECT t.finished_at, t.passed, t.total, p.name as project_name FROM tests t
        JOIN projects p ON t.project_id = p.id
        WHERE t.status='completed' AND t.total > 0
        ORDER BY t.finished_at DESC LIMIT 12
    """).fetchall()
    trend_rows = list(reversed(trend_rows))
    trend_labels = [f"{r['project_name']} ({(r['finished_at'] or '')[:10]})" for r in trend_rows]
    trend_values = [round((r["passed"] / r["total"]) * 100) for r in trend_rows]

    # Chart: results per project
    proj_rows = db.execute("""
        SELECT p.name,
            COALESCE(SUM(t.passed),0) as ps,
            COALESCE(SUM(t.failed),0) as fs,
            COALESCE(SUM(t.warning),0) as ws
        FROM projects p
        LEFT JOIN tests t ON t.project_id = p.id AND t.status='completed'
        GROUP BY p.id ORDER BY p.name
    """).fetchall()

    return render_template("dashboard.html",
        total_projects=total_projects,
        active_projects=active_projects,
        total_tests=total_tests,
        total_passed=total_passed,
        total_failed=total_failed,
        success_rate=success_rate,
        fail_rate=fail_rate,
        last_test=last_test,
        recent_tests=recent_tests,
        chart_status=status_dist,
        chart_trend_labels=trend_labels,
        chart_trend_values=trend_values,
        chart_proj_labels=[r["name"] for r in proj_rows],
        chart_proj_pass=[r["ps"] for r in proj_rows],
        chart_proj_fail=[r["fs"] for r in proj_rows],
        chart_proj_warn=[r["ws"] for r in proj_rows],
    )


# ---- Projects ----

@app.route("/projects")
@login_required
def projects():
    db = get_db()
    projects_list = db.execute("""
        SELECT p.*,
            (SELECT COUNT(*) FROM tests t WHERE t.project_id=p.id AND t.status='completed') as test_count,
            (SELECT COALESCE(SUM(t.passed),0) FROM tests t WHERE t.project_id=p.id AND t.status='completed') as pass_count,
            (SELECT COALESCE(SUM(t.passed+t.failed+t.warning+t.skipped),0) FROM tests t WHERE t.project_id=p.id AND t.status='completed') as total_count,
            (SELECT t.started_at FROM tests t WHERE t.project_id=p.id AND t.status IN ('completed','failed') ORDER BY t.finished_at DESC LIMIT 1) as last_test_date
        FROM projects p ORDER BY p.updated_at DESC
    """).fetchall()
    return render_template("projects.html", projects=projects_list)


@app.route("/projects/new", methods=["GET", "POST"])
@login_required
def project_new():
    if request.method == "POST":
        name = request.form["name"].strip()
        url_val = request.form["url"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]
        auth_type = request.form.get("auth_type", "simple")
        comments = request.form.get("comments", "").strip()

        if not name or not url_val:
            flash("Le nom et l'URL sont obligatoires", "error")
            return render_template("project-form.html", project=None, editing=False)

        db = get_db()
        db.execute(
            "INSERT INTO projects (name, url, email, password_enc, auth_type, comments, created_by, updated_by) VALUES (?,?,?,?,?,?,?,?)",
            (name, url_val, email, encrypt_value(password), auth_type, comments,
             session.get("user_email", ""), session.get("user_email", ""))
        )
        db.commit()
        flash("Projet créé avec succès", "success")
        return redirect(url_for("projects"))

    return render_template("project-form.html", project=None, editing=False)


@app.route("/projects/<int:pid>")
@login_required
def project_detail(pid):
    db = get_db()
    project = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))

    tests = db.execute("""
        SELECT * FROM tests WHERE project_id=? ORDER BY started_at DESC
    """, (pid,)).fetchall()

    # Discovered modules from last exploration
    pages_count = db.execute(
        "SELECT COUNT(*) FROM project_pages WHERE project_id=?", (pid,)
    ).fetchone()[0]

    # Compute success rate from latest completed test
    latest = db.execute(
        "SELECT * FROM tests WHERE project_id=? AND status='completed' ORDER BY finished_at DESC LIMIT 1",
        (pid,)
    ).fetchone()
    success_pct = 0
    if latest and latest["total"] > 0:
        success_pct = round((latest["passed"] / latest["total"]) * 100)

    return render_template("project-detail.html",
        project=project, tests=tests, success_pct=success_pct, latest=latest,
        pages_count=pages_count)


@app.route("/projects/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def project_edit(pid):
    db = get_db()
    project = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))

    if request.method == "POST":
        name = request.form["name"].strip()
        url_val = request.form["url"].strip()
        email = request.form["email"].strip()
        password = request.form.get("password", "")
        auth_type = request.form.get("auth_type", "simple")
        comments = request.form.get("comments", "").strip()

        if not name or not url_val:
            flash("Le nom et l'URL sont obligatoires", "error")
            return render_template("project-form.html", project=project, editing=True)

        if password:
            pw_enc = encrypt_value(password)
        else:
            pw_enc = project["password_enc"]

        db.execute(
            "UPDATE projects SET name=?, url=?, email=?, password_enc=?, auth_type=?, comments=?, updated_by=?, updated_at=datetime('now') WHERE id=?",
            (name, url_val, email, pw_enc, auth_type, comments,
             session.get("user_email", ""), pid)
        )
        db.commit()
        flash("Projet mis à jour", "success")
        return redirect(url_for("project_detail", pid=pid))

    return render_template("project-form.html", project=project, editing=True)


@app.route("/projects/<int:pid>/toggle", methods=["POST"])
@login_required
def project_toggle(pid):
    db = get_db()
    project = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not project:
        return jsonify({"error": "Not found"}), 404
    new_status = "disabled" if project["status"] == "active" else "active"
    db.execute("UPDATE projects SET status=?, updated_at=datetime('now') WHERE id=?", (new_status, pid))
    db.commit()
    return jsonify({"status": new_status})


@app.route("/projects/<int:pid>/delete", methods=["POST"])
@login_required
def project_delete(pid):
    db = get_db()
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    db.commit()
    flash("Projet supprimé", "success")
    return redirect(url_for("projects"))


# ---- Tests ----

@app.route("/projects/<int:pid>/explore", methods=["POST"])
@login_required
def project_explore(pid):
    """Step 1: crawl the application to discover all pages/modules."""
    db = get_db()
    project = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))
    if project["status"] != "active":
        flash("Ce projet est désactivé", "error")
        return redirect(url_for("project_detail", pid=pid))

    cur = db.execute(
        "INSERT INTO tests (project_id, status, created_by) VALUES (?, 'exploring', ?)",
        (pid, session.get("user_email", ""))
    )
    db.commit()
    test_id = cur.lastrowid

    thread = threading.Thread(target=run_exploration, args=(test_id, pid), daemon=True)
    thread.start()

    return redirect(url_for("test_view", tid=test_id))


@app.route("/projects/<int:pid>/modules", methods=["GET"])
@login_required
def project_modules(pid):
    """Step 2: select with checkboxes the pages AND actions to test."""
    db = get_db()
    project = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))

    pages = db.execute(
        "SELECT * FROM project_pages WHERE project_id=? ORDER BY id", (pid,)
    ).fetchall()

    # Group inventoried actions by page
    actions = db.execute(
        "SELECT * FROM page_actions WHERE project_id=? ORDER BY id", (pid,)
    ).fetchall()

    by_url = {}
    for pg in pages:
        by_url[pg["url"]] = {"page": pg, "actions": []}
    for act in actions:
        if act["page_url"] in by_url:
            by_url[act["page_url"]]["actions"].append(act)
        else:
            # Action on a page no longer listed (defensive)
            if act["page_url"] not in by_url:
                by_url[act["page_url"]] = {
                    "page": {"id": None, "name": act["page_name"], "url": act["page_url"],
                             "last_status": 0},
                    "actions": [],
                }
            by_url[act["page_url"]]["actions"].append(act)

    page_groups = list(by_url.values())
    total_actions = sum(len(g["actions"]) for g in page_groups)

    return render_template("module-select.html", project=project,
                           page_groups=page_groups, total_actions=total_actions)


@app.route("/projects/<int:pid>/run", methods=["POST"])
@login_required
def test_run(pid):
    """Step 3: run QA tests on the selected modules."""
    db = get_db()
    project = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))
    if project["status"] != "active":
        flash("Ce projet est désactivé", "error")
        return redirect(url_for("project_detail", pid=pid))

    action_ids = request.form.getlist("action_ids")
    selected = []
    if action_ids:
        placeholders = ",".join("?" for _ in action_ids)
        rows = db.execute(
            f"""SELECT page_url, page_name, action_type, label, el_index
                FROM page_actions WHERE project_id=? AND id IN ({placeholders})
                ORDER BY id""",
            [pid] + [int(a) for a in action_ids]
        ).fetchall()
        selected = [{
            "page_url": r["page_url"],
            "page_name": r["page_name"],
            "action_type": r["action_type"],
            "label": r["label"],
            "el_index": r["el_index"],
        } for r in rows]

    if not selected:
        flash("Veuillez d'abord explorer l'application et selectionner au moins une action a tester", "error")
        return redirect(url_for("project_modules", pid=pid))

    cur = db.execute(
        "INSERT INTO tests (project_id, status, selected_modules, selected_actions, created_by) VALUES (?, 'running', ?, ?, ?)",
        (pid,
         json.dumps([{"name": u["page_name"], "url": u["page_url"]} for u in {
            (a["page_url"]): a for a in selected}.values()], ensure_ascii=False),
         json.dumps(selected, ensure_ascii=False),
         session.get("user_email", ""))
    )
    db.commit()
    test_id = cur.lastrowid

    thread = threading.Thread(target=run_qa_test, args=(test_id, pid), daemon=True)
    thread.start()

    return redirect(url_for("test_view", tid=test_id))


@app.route("/tests/<int:tid>")
@login_required
def test_view(tid):
    db = get_db()
    test = db.execute("""
        SELECT t.*, p.name as project_name, p.id as project_id
        FROM tests t JOIN projects p ON t.project_id = p.id
        WHERE t.id=?
    """, (tid,)).fetchone()
    if not test:
        flash("Test introuvable", "error")
        return redirect(url_for("projects"))

    results = db.execute(
        "SELECT * FROM results WHERE test_id=? ORDER BY id", (tid,)
    ).fetchall()

    return render_template("test.html", test=test, results=results)


@app.route("/tests/<int:tid>/otp", methods=["POST"])
@login_required
def test_otp(tid):
    db = get_db()
    otp_code = request.form.get("otp_code", "").strip()
    if not otp_code:
        flash("Veuillez saisir le code OTP", "error")
        return redirect(url_for("test_view", tid=tid))
    db.execute(
        "UPDATE tests SET status='otp_submitted' WHERE id=? AND status='waiting_otp'",
        (tid,)
    )
    db.execute(
        "UPDATE tests SET otp_code=? WHERE id=?",
        (otp_code, tid)
    )
    db.commit()
    flash("Code OTP envoyé", "success")
    return redirect(url_for("test_view", tid=tid))


@app.route("/tests/<int:tid>/cancel", methods=["POST"])
@login_required
def test_cancel(tid):
    db = get_db()
    # Ask the engine to stop: the background thread detects 'cancel_requested',
    # closes the browser and finalizes the test as 'cancelled'.
    db.execute(
        "UPDATE tests SET status='cancel_requested' WHERE id=? AND status IN ('running','waiting_otp','exploring')",
        (tid,)
    )
    db.commit()
    flash("Arret du test demande...", "info")
    return redirect(url_for("test_view", tid=tid))


# ---- Users Management ----

@app.route("/users")
@admin_required
def users_list():
    db = get_db()
    all_users = db.execute("SELECT id, email, created_at FROM users ORDER BY created_at DESC").fetchall()
    return render_template("users.html", users=all_users)


@app.route("/users/new", methods=["GET", "POST"])
@admin_required
def user_new():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        confirm = request.form.get("confirm_password", "").strip()

        if not email or not password:
            flash("L'email et le mot de passe sont obligatoires", "error")
            return render_template("user-form.html", editing=False)

        if password != confirm:
            flash("Les mots de passe ne correspondent pas", "error")
            return render_template("user-form.html", editing=False)

        if len(password) < 6:
            flash("Le mot de passe doit faire au moins 6 caracteres", "error")
            return render_template("user-form.html", editing=False)

        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            flash("Cet email est deja utilise", "error")
            return render_template("user-form.html", editing=False)

        db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, hash_password(password))
        )
        db.commit()
        flash(f"Utilisateur {email} cree avec succes", "success")
        return redirect(url_for("users_list"))

    return render_template("user-form.html", editing=False)


@app.route("/users/<int:uid>/delete", methods=["POST"])
@admin_required
def user_delete(uid):
    db = get_db()
    user = db.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        flash("Utilisateur introuvable", "error")
        return redirect(url_for("users_list"))

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    if user["email"] == admin_email:
        flash("Impossible de supprimer l'administrateur principal", "error")
        return redirect(url_for("users_list"))

    if uid == session.get("user_id"):
        flash("Impossible de vous supprimer vous-meme", "error")
        return redirect(url_for("users_list"))

    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    flash(f"Utilisateur {user['email']} supprime", "success")
    return redirect(url_for("users_list"))


# ---- Reports ----

@app.route("/reports")
@login_required
def reports():
    db = get_db()
    all_tests = db.execute("""
        SELECT t.*, p.name as project_name FROM tests t
        JOIN projects p ON t.project_id = p.id
        WHERE t.status IN ('completed','failed')
        ORDER BY t.finished_at DESC
    """).fetchall()
    return render_template("reports.html", tests=all_tests)


@app.route("/reports/<int:tid>")
@login_required
def report_detail(tid):
    db = get_db()
    test = db.execute("""
        SELECT t.*, p.name as project_name, p.id as project_id
        FROM tests t JOIN projects p ON t.project_id = p.id
        WHERE t.id=?
    """, (tid,)).fetchone()
    if not test:
        flash("Rapport introuvable", "error")
        return redirect(url_for("reports"))

    results = db.execute(
        "SELECT * FROM results WHERE test_id=? ORDER BY id", (tid,)
    ).fetchall()

    # Group results by module + compute stats for charts
    modules = {}
    module_stats = []
    severity_counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0}

    for r in results:
        mod = r["module"]
        if mod not in modules:
            modules[mod] = []
        modules[mod].append(r)

        if r["status"] == "FAIL" and r["severity"]:
            sev = r["severity"].upper()
            if sev in severity_counts:
                severity_counts[sev] += 1

    for mod_name, mod_results in modules.items():
        module_stats.append({
            "name": mod_name,
            "total": len(mod_results),
            "pass": sum(1 for r in mod_results if r["status"] == "PASS"),
            "fail": sum(1 for r in mod_results if r["status"] == "FAIL"),
            "warning": sum(1 for r in mod_results if r["status"] == "WARNING"),
            "skipped": sum(1 for r in mod_results if r["status"] == "SKIPPED"),
        })

    # HTTP status codes of each tested page
    page_statuses = []
    seen_pages = set()
    for r in results:
        if r["function_name"] == "Acces au module" and r["module"] not in seen_pages:
            seen_pages.add(r["module"])
            page_statuses.append({
                "name": r["module"],
                "status": r["http_status"],
            })
    # Complete with pages discovered during exploration not tested in this run
    extra = db.execute("""
        SELECT pp.name, pp.last_status as status FROM project_pages pp
        WHERE pp.project_id=?
    """, (test["project_id"],)).fetchall()
    for e in extra:
        if e["name"] not in seen_pages:
            page_statuses.append({"name": e["name"], "status": e["status"]})

    return render_template("report.html", test=test, results=results,
                           modules=modules, module_stats=module_stats,
                           severity_counts=severity_counts,
                           page_statuses=page_statuses)


# ---------------------------------------------------------------------------
# Init & Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
