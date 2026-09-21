import sqlite3
import hashlib
import json
import os
import secrets
from datetime import datetime

from .config import env
from .models import (User, Project, ProjectPage, PageAction,
                     TestRun, TestResult)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT DEFAULT '',
    role TEXT NOT NULL DEFAULT 'qa',
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    email TEXT NOT NULL,
    password_enc TEXT NOT NULL,
    auth_type TEXT NOT NULL DEFAULT 'simple',
    environment TEXT NOT NULL DEFAULT 'STAGING',
    comments TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    owner_id INTEGER DEFAULT NULL,
    report_recipients TEXT DEFAULT '',
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
    action_key TEXT DEFAULT '',
    x INTEGER DEFAULT 0,
    y INTEGER DEFAULT 0,
    el_index INTEGER DEFAULT 0,
    sensitive INTEGER DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    run_type TEXT NOT NULL DEFAULT 'Test',
    started_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    total INTEGER DEFAULT 0,
    passed INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    warning INTEGER DEFAULT 0,
    skipped INTEGER DEFAULT 0,
    report_json TEXT DEFAULT '{}',
    plan_json TEXT DEFAULT '[]',
    otp_code TEXT DEFAULT '',
    launched_by TEXT DEFAULT '',
    owner_id INTEGER DEFAULT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id INTEGER NOT NULL,
    module TEXT NOT NULL,
    function_name TEXT NOT NULL,
    action TEXT DEFAULT '',
    data TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PASS',
    severity TEXT DEFAULT '',
    expected TEXT DEFAULT '',
    obtained TEXT DEFAULT '',
    screenshot TEXT DEFAULT '',
    http_status INTEGER DEFAULT 0,
    FOREIGN KEY (test_id) REFERENCES test_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS test_modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    url TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS test_functionalities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    path TEXT DEFAULT '',
    url TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (module_id) REFERENCES test_modules(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS test_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    functionality_id INTEGER NOT NULL,
    step_order INTEGER DEFAULT 0,
    description TEXT NOT NULL,
    action_type TEXT DEFAULT '',
    target TEXT DEFAULT '',
    value TEXT DEFAULT '',
    expected TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (functionality_id) REFERENCES test_functionalities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient TEXT NOT NULL,
    subject TEXT DEFAULT '',
    body_html TEXT DEFAULT '',
    sent_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scheduled_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    interval_minutes INTEGER DEFAULT 60,
    time_of_day TEXT DEFAULT '08:00',
    day_of_week TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    last_run_at TEXT DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE (project_id, user_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""


def get_connection(path=None):
    db = sqlite3.connect(path or env.DATABASE_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def init_db(path=None):
    db = get_connection(path)
    db.executescript(SCHEMA)

    # Lightweight migration: add el_index to page_actions for pre-existing DBs
    cols = [r[1] for r in db.execute("PRAGMA table_info(page_actions)").fetchall()]
    if "el_index" not in cols:
        db.execute("ALTER TABLE page_actions ADD COLUMN el_index INTEGER DEFAULT 0")

    # Migration: add 'path' (navigation sub-folder path, e.g. "Facturation > Contrats")
    # to test_functionalities for pre-existing databases.
    fcols = [r[1] for r in db.execute("PRAGMA table_info(test_functionalities)").fetchall()]
    if "path" not in fcols:
        db.execute("ALTER TABLE test_functionalities ADD COLUMN path TEXT DEFAULT ''")
    if "url" not in fcols:
        db.execute("ALTER TABLE test_functionalities ADD COLUMN url TEXT DEFAULT ''")

    # Migration: add 'url' (direct module URL, e.g. .../crm) so a re-scan can
    # navigate straight to the module instead of guessing from the dashboard.
    mcols = [r[1] for r in db.execute("PRAGMA table_info(test_modules)").fetchall()]
    if "url" not in mcols:
        db.execute("ALTER TABLE test_modules ADD COLUMN url TEXT DEFAULT ''")

    # Migration: add structured action columns to test_steps
    scols = [r[1] for r in db.execute("PRAGMA table_info(test_steps)").fetchall()]
    for col, default in [("action_type", ""), ("target", ""),
                         ("value", ""), ("expected", "")]:
        if col not in scols:
            db.execute(f"ALTER TABLE test_steps ADD COLUMN {col} TEXT DEFAULT '{default}'")

    # Migration: user roles / names for pre-existing databases
    ucols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    if "role" not in ucols:
        db.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'qa'")
    if "is_active" not in ucols:
        db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
    if "full_name" not in ucols:
        db.execute("ALTER TABLE users ADD COLUMN full_name TEXT DEFAULT ''")

    # Migration: project / test ownership (owner_id) for user isolation
    pcols = [r[1] for r in db.execute("PRAGMA table_info(projects)").fetchall()]
    if "owner_id" not in pcols:
        db.execute("ALTER TABLE projects ADD COLUMN owner_id INTEGER DEFAULT NULL")
    if "report_recipients" not in pcols:
        db.execute(
            "ALTER TABLE projects ADD COLUMN report_recipients TEXT DEFAULT ''")
    tcols = [r[1] for r in db.execute("PRAGMA table_info(test_runs)").fetchall()]
    if "owner_id" not in tcols:
        db.execute("ALTER TABLE test_runs ADD COLUMN owner_id INTEGER DEFAULT NULL")
    if "otp_code" not in tcols:
        db.execute("ALTER TABLE test_runs ADD COLUMN otp_code TEXT DEFAULT ''")

    # Bootstrap admin if no user exists
    cur = db.execute("SELECT id FROM users LIMIT 1")
    if cur.fetchone() is None:
        db.execute(
            "INSERT INTO users (email, password_hash, full_name, role) VALUES (?, ?, ?, 'admin')",
            (env.ADMIN_EMAIL, hash_password(env.ADMIN_PASSWORD), "Administrateur"))
        db.commit()
    else:
        # Promote the configured admin email, and guarantee at least one admin
        db.execute("UPDATE users SET role='admin' WHERE email=?", (env.ADMIN_EMAIL,))
        any_admin = db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
        if not any_admin:
            first = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
            if first:
                db.execute("UPDATE users SET role='admin' WHERE id=?", (first["id"],))
        db.commit()

    # Backfill ownership: existing data belongs to the first admin by default
    adm = db.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
    if adm:
        db.execute("UPDATE projects SET owner_id=? WHERE owner_id IS NULL", (adm["id"],))
        db.execute(
            "UPDATE test_runs SET owner_id=COALESCE(owner_id, "
            "(SELECT owner_id FROM projects WHERE projects.id=test_runs.project_id), ?) "
            "WHERE owner_id IS NULL",
            (adm["id"],))
        db.commit()

    # Migration: scheduled_runs — add interval_minutes, day_of_week
    if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_runs'").fetchone():
        scols = [r[1] for r in db.execute("PRAGMA table_info(scheduled_runs)").fetchall()]
        if "interval_minutes" not in scols:
            db.execute("ALTER TABLE scheduled_runs ADD COLUMN interval_minutes INTEGER DEFAULT 60")
        if "day_of_week" not in scols:
            db.execute("ALTER TABLE scheduled_runs ADD COLUMN day_of_week TEXT DEFAULT ''")

    db.close()
    return True


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_user_by_credentials(email, password):
    db = get_connection()
    row = db.execute(
        "SELECT * FROM users WHERE email=? AND password_hash=? AND is_active=1",
        (email, hash_password(password))).fetchone()
    db.close()
    return User.from_row(row) if row else None


def get_user(email):
    db = get_connection()
    row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    db.close()
    return User.from_row(row) if row else None


def get_user_by_id(uid):
    db = get_connection()
    row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    db.close()
    return User.from_row(row) if row else None


def get_user_by_email(email):
    db = get_connection()
    row = db.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    db.close()
    return User.from_row(row) if row else None


def list_users():
    db = get_connection()
    rows = db.execute("SELECT * FROM users ORDER BY id").fetchall()
    db.close()
    return [User.from_row(r) for r in rows]


def create_user(email, password, full_name="", role="qa"):
    email = email.strip().lower()
    db = get_connection()
    exists = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if exists:
        db.close()
        return None
    cur = db.execute(
        "INSERT INTO users (email, password_hash, full_name, role) VALUES (?,?,?,?)",
        (email, hash_password(password), full_name.strip(), role or "qa"))
    db.commit()
    uid = cur.lastrowid
    db.close()
    return uid


def delete_user(uid):
    db = get_connection()
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    db.close()


def set_user_role(uid, role):
    db = get_connection()
    db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    db.commit()
    db.close()


def validate_credentials(email, password):
    """True when the (active) user & password match. Used e.g. before changing
    the password from the profile."""
    db = get_connection()
    row = db.execute(
        "SELECT password_hash FROM users WHERE email=? AND is_active=1",
        (email,)).fetchone()
    db.close()
    return bool(row) and row["password_hash"] == hash_password(password)


def update_user(uid, full_name=None, email=None, role=None):
    """Update profile info. Email must stay unique (returns False otherwise)."""
    db = get_connection()
    cur = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not cur:
        db.close()
        return None
    if email is not None:
        email = email.strip().lower()
        taken = db.execute(
            "SELECT id FROM users WHERE email=? AND id<>?", (email, uid)).fetchone()
        if taken:
            db.close()
            return False
    db.execute(
        "UPDATE users SET full_name=?, email=?, role=? WHERE id=?",
        (cur["full_name"] if full_name is None else full_name.strip(),
         cur["email"] if email is None else email,
         cur["role"] if role is None else role,
         uid))
    db.commit()
    db.close()
    return True


def update_user_password(uid, new_password):
    db = get_connection()
    db.execute("UPDATE users SET password_hash=? WHERE id=?",
               (hash_password(new_password), uid))
    db.commit()
    db.close()


def set_user_active(uid, active):
    db = get_connection()
    db.execute("UPDATE users SET is_active=? WHERE id=?",
               (1 if active else 0, uid))
    db.commit()
    db.close()


def count_active_users():
    db = get_connection()
    n = db.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
    db.close()
    return n


def create_password_reset(email):
    """Create a reset token for the given e-mail and return the plain token,
    or None when the address does not belong to an active user."""
    db = get_connection()
    row = db.execute(
        "SELECT id FROM users WHERE email=? AND is_active=1",
        (email.strip().lower(),)).fetchone()
    if not row:
        db.close()
        return None
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM password_resets WHERE user_id=?", (row["id"],))
    db.execute(
        "INSERT INTO password_resets (user_id, token_hash, expires_at) "
        "VALUES (?, ?, datetime('now', '+60 minutes'))",
        (row["id"], hash_password(token)))
    db.commit()
    db.close()
    return token


def get_password_reset(token):
    """Return the user id behind a valid (unexpired) token, else None."""
    db = get_connection()
    hash_digest = hash_password(token)
    row = db.execute(
        "SELECT user_id, expires_at FROM password_resets "
        "WHERE token_hash=? AND expires_at > datetime('now')",
        (hash_digest,)).fetchone()
    db.close()
    return row["user_id"] if row else None


def consume_password_reset(token):
    db = get_connection()
    db.execute("DELETE FROM password_resets WHERE token_hash=?",
               (hash_password(token),))
    db.commit()
    db.close()


def queue_outbox(recipient, subject, body_html):
    """Record a generated e-mail. Mails are delivered by the mailer when SMTP
    is configured, and always kept here as a trace (and for tests)."""
    db = get_connection()
    db.execute(
        "INSERT INTO outbox (recipient, subject, body_html) VALUES (?,?,?)",
        (recipient, subject, body_html))
    db.commit()
    db.close()


def list_outbox():
    db = get_connection()
    rows = db.execute("SELECT * FROM outbox ORDER BY id DESC").fetchall()
    db.close()
    return [dict(r) for r in rows]


def count_admins():
    db = get_connection()
    n = db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1").fetchone()[0]
    db.close()
    return n


# ---------------------------------------------------------------------------
# Scheduled runs (auto-execution of scenarios)
# ---------------------------------------------------------------------------

def list_scheduled_runs(owner_id=None):
    db = get_connection()
    if owner_id:
        rows = db.execute(
            "SELECT s.*, p.name as project_name FROM scheduled_runs s "
            "JOIN projects p ON s.project_id=p.id "
            "WHERE p.owner_id=? OR p.id IN "
            "(SELECT project_id FROM project_members WHERE user_id=?) "
            "ORDER BY s.id", (owner_id, owner_id)).fetchall()
    else:
        rows = db.execute(
            "SELECT s.*, p.name as project_name FROM scheduled_runs s "
            "JOIN projects p ON s.project_id=p.id ORDER BY s.id").fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_scheduled_run(sid):
    db = get_connection()
    row = db.execute("SELECT * FROM scheduled_runs WHERE id=?", (sid,)).fetchone()
    db.close()
    return dict(row) if row else None


def create_scheduled_run(project_id, interval_minutes=60, time_of_day="08:00",
                         day_of_week=""):
    db = get_connection()
    cur = db.execute(
        "INSERT INTO scheduled_runs (project_id, interval_minutes, time_of_day, day_of_week) "
        "VALUES (?, ?, ?, ?)",
        (project_id, interval_minutes, time_of_day, day_of_week))
    db.commit()
    sid = cur.lastrowid
    db.close()
    return sid


def update_scheduled_run(sid, **kwargs):
    db = get_connection()
    allowed = {"interval_minutes", "time_of_day", "is_active", "day_of_week"}
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if sets:
        vals.append(sid)
        db.execute(f"UPDATE scheduled_runs SET {', '.join(sets)} WHERE id=?", vals)
        db.commit()
    db.close()


def delete_scheduled_run(sid):
    db = get_connection()
    db.execute("DELETE FROM scheduled_runs WHERE id=?", (sid,))
    db.commit()
    db.close()


def mark_scheduled_run_done(sid):
    db = get_connection()
    db.execute(
        "UPDATE scheduled_runs SET last_run_at=datetime('now') WHERE id=?", (sid,))
    db.commit()
    db.close()


def get_due_scheduled_runs():
    """Return scheduled runs that are due based on interval_minutes and last_run_at."""
    db = get_connection()
    rows = db.execute(
        "SELECT s.*, p.name as project_name, p.status as project_status "
        "FROM scheduled_runs s "
        "JOIN projects p ON s.project_id=p.id "
        "WHERE s.is_active=1 AND p.status='active'").fetchall()
    due = []
    now = datetime.now()
    weekday = now.weekday()  # 0=Monday .. 6=Sunday
    _DAY_NAMES = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    for r in rows:
        d = dict(r)
        last = d.get("last_run_at")
        interval = d.get("interval_minutes") or 60
        tod = d.get("time_of_day", "08:00")
        day_of_week = (d.get("day_of_week") or "").strip().lower()
        try:
            th, tm = map(int, tod.split(":"))
        except (ValueError, AttributeError):
            th, tm = 8, 0

        # 1) Interval mode: run every N minutes
        if interval > 0 and interval <= 1440:  # up to 24h
            if not last:
                due.append(d)
            else:
                try:
                    lr = datetime.fromisoformat(last)
                    elapsed = (now - lr).total_seconds()
                    if elapsed >= interval * 60 - 30:  # -30s tolerance
                        due.append(d)
                except (ValueError, TypeError):
                    due.append(d)

        # 2) Day-specific mode: run on selected days at time_of_day
        elif interval > 1440 or day_of_week:
            today_name = _DAY_NAMES[weekday]
            selected_days = [x.strip() for x in day_of_week.split(",") if x.strip()]
            if today_name in selected_days:
                if now.hour > th or (now.hour == th and now.minute >= tm):
                    if not last:
                        due.append(d)
                    else:
                        try:
                            lr = datetime.fromisoformat(last)
                            if lr.date() != now.date():
                                due.append(d)
                        except (ValueError, TypeError):
                            due.append(d)

    db.close()
    return due


# ---------------------------------------------------------------------------
# Test scenarios: modules -> functionalities -> steps (manual SDET)
# ---------------------------------------------------------------------------

def list_modules(pid):
    db = get_connection()
    rows = db.execute(
        """SELECT m.*,
            (SELECT COUNT(*) FROM test_functionalities f WHERE f.module_id=m.id) as functionality_count,
            (SELECT COUNT(*) FROM test_steps s
               JOIN test_functionalities f ON s.functionality_id=f.id
               WHERE f.module_id=m.id) as step_count
           FROM test_modules m WHERE m.project_id=? ORDER BY m.id""", (pid,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_module(mid):
    db = get_connection()
    row = db.execute("SELECT * FROM test_modules WHERE id=?", (mid,)).fetchone()
    db.close()
    return dict(row) if row else None


def create_module(pid, name, description="", url=""):
    db = get_connection()
    cur = db.execute(
        "INSERT INTO test_modules (project_id, name, description, url) VALUES (?,?,?,?)",
        (pid, name, description, url))
    db.commit()
    mid = cur.lastrowid
    db.close()
    return mid


def delete_module(mid):
    db = get_connection()
    db.execute("DELETE FROM test_modules WHERE id=?", (mid,))
    db.commit()
    db.close()


def update_module(mid, name, description="", url=""):
    db = get_connection()
    db.execute(
        "UPDATE test_modules SET name=?, description=?, url=? WHERE id=?",
        (name, description, url, mid))
    db.commit()
    db.close()


def list_functionalities(mid):
    db = get_connection()
    rows = db.execute(
        "SELECT * FROM test_functionalities WHERE module_id=? ORDER BY id", (mid,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def list_all_functionalities(pid):
    db = get_connection()
    rows = db.execute(
        """SELECT f.*, m.name as module_name, m.id as module_id
           FROM test_functionalities f
           JOIN test_modules m ON f.module_id=m.id
           WHERE m.project_id=? ORDER BY m.id, f.id""", (pid,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_functionality(fid):
    db = get_connection()
    row = db.execute("SELECT * FROM test_functionalities WHERE id=?", (fid,)).fetchone()
    db.close()
    return dict(row) if row else None


def create_functionality(mid, name, description="", path="", url=""):
    db = get_connection()
    cur = db.execute(
        "INSERT INTO test_functionalities (module_id, name, description, path, url) VALUES (?,?,?,?,?)",
        (mid, name, description, path, url))
    db.commit()
    fid = cur.lastrowid
    db.close()
    return fid


def delete_functionality(fid):
    db = get_connection()
    db.execute("DELETE FROM test_functionalities WHERE id=?", (fid,))
    db.commit()
    db.close()


def toggle_functionality(fid):
    """Toggle is_active for a functionality. Returns new state (0 or 1)."""
    db = get_connection()
    row = db.execute("SELECT is_active FROM test_functionalities WHERE id=?", (fid,)).fetchone()
    if not row:
        db.close()
        return None
    new_val = 0 if row["is_active"] else 1
    db.execute("UPDATE test_functionalities SET is_active=? WHERE id=?", (new_val, fid))
    db.commit()
    db.close()
    return new_val


def get_last_functionality_status(fid):
    """Get the last test run result status for a functionality.
    Returns (status, date) or (None, None) if never run.
    """
    db = get_connection()
    row = db.execute(
        """SELECT r.status, t.finished_at
           FROM test_results r
           JOIN test_runs t ON r.test_id = t.id
           JOIN test_functionalities f ON r.function_name = f.name
           WHERE f.id = ?
           ORDER BY t.id DESC LIMIT 1""", (fid,)).fetchone()
    db.close()
    if row:
        return (row["status"], row["finished_at"])
    return (None, None)


def list_steps(fid):
    db = get_connection()
    rows = db.execute(
        "SELECT * FROM test_steps WHERE functionality_id=? ORDER BY step_order, id",
        (fid,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def add_step(fid, description, action_type="", target="", value="", expected=""):
    db = get_connection()
    nxt = db.execute(
        "SELECT COALESCE(MAX(step_order),0)+1 FROM test_steps WHERE functionality_id=?",
        (fid,)).fetchone()[0]
    cur = db.execute(
        """INSERT INTO test_steps
           (functionality_id, step_order, description, action_type, target, value, expected)
           VALUES (?,?,?,?,?,?,?)""",
        (fid, nxt, description, action_type, target, value, expected))
    db.commit()
    sid = cur.lastrowid
    db.close()
    return sid


def delete_step(sid):
    db = get_connection()
    db.execute("DELETE FROM test_steps WHERE id=?", (sid,))
    db.commit()
    db.close()


def update_functionality(fid, name, description="", path="", url=""):
    db = get_connection()
    db.execute("UPDATE test_functionalities SET name=?, description=?, path=?, url=? WHERE id=?",
               (name, description, path, url, fid))
    db.commit()
    db.close()


def update_step(sid, description, action_type=None, target=None, value=None,
                expected=None):
    db = get_connection()
    if action_type is not None:
        db.execute(
            """UPDATE test_steps SET description=?, action_type=?,
               target=?, value=?, expected=? WHERE id=?""",
            (description, action_type, target or "", value or "",
             expected or "", sid))
    else:
        db.execute("UPDATE test_steps SET description=? WHERE id=?",
                   (description, sid))
    db.commit()
    db.close()


def get_step(sid):
    db = get_connection()
    row = db.execute("SELECT * FROM test_steps WHERE id=?", (sid,)).fetchone()
    db.close()
    return dict(row) if row else None


def scenario_tree(pid):
    """Return nested structure: modules with their functionalities and steps."""
    modules = list_modules(pid)
    for m in modules:
        fns = list_functionalities(m["id"])
        for f in fns:
            f["steps"] = list_steps(f["id"])
            status, date = get_last_functionality_status(f["id"])
            f["last_status"] = status
            f["last_run_date"] = date
        m["functionalities"] = fns
    return modules


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def list_projects(owner_id=None):
    db = get_connection()
    where = ""
    args = ()
    if owner_id is not None:
        where = (" WHERE p.owner_id=? OR p.id IN "
                 "(SELECT project_id FROM project_members WHERE user_id=?)")
        args = (owner_id, owner_id)
    rows = db.execute(
        """SELECT p.*,
            (SELECT COUNT(*) FROM test_runs t WHERE t.project_id=p.id AND t.status='completed') as test_count,
            (SELECT COALESCE(SUM(t.passed),0) FROM test_runs t WHERE t.project_id=p.id AND t.status='completed') as pass_count,
            (SELECT COALESCE(SUM(t.failed),0) FROM test_runs t WHERE t.project_id=p.id AND t.status='completed') as fail_count,
            (SELECT COALESCE(SUM(t.warning),0) FROM test_runs t WHERE t.project_id=p.id AND t.status='completed') as warn_count,
            (SELECT COALESCE(SUM(t.passed+t.failed+t.warning+t.skipped),0) FROM test_runs t WHERE t.project_id=p.id AND t.status='completed') as total_count,
            (SELECT t.finished_at FROM test_runs t WHERE t.project_id=p.id AND t.status IN ('completed','failed') ORDER BY t.finished_at DESC LIMIT 1) as last_test_date
            FROM projects p""" + where + " ORDER BY p.updated_at DESC", args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def list_project_members(pid):
    db = get_connection()
    rows = db.execute(
        "SELECT pm.user_id, u.email, u.full_name FROM project_members pm "
        "JOIN users u ON u.id=pm.user_id WHERE pm.project_id=? ORDER BY u.email",
        (pid,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def project_member_ids(pid):
    return [m["user_id"] for m in list_project_members(pid)]


def replace_project_members(pid, user_ids):
    db = get_connection()
    db.execute("DELETE FROM project_members WHERE project_id=?", (pid,))
    seen = set()
    for uid in (user_ids or []):
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            continue
        if uid in seen:
            continue
        seen.add(uid)
        db.execute("INSERT OR IGNORE INTO project_members (project_id, user_id) VALUES (?,?)",
                   (pid, uid))
    db.commit()
    db.close()


def project_is_visible(pid, uid):
    if uid is None:
        return True
    db = get_connection()
    row = db.execute(
        "SELECT id FROM projects WHERE id=? AND (owner_id=? OR id IN "
        "(SELECT project_id FROM project_members WHERE user_id=?))",
        (pid, uid, uid)).fetchone()
    db.close()
    return row is not None


def get_project(pid):
    db = get_connection()
    row = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    db.close()
    return Project.from_row(row) if row else None


def create_project(data, editor="", encrypt=None, decrypt=None, owner_id=None):
    db = get_connection()
    cur = db.execute(
        """INSERT INTO projects
           (name, url, email, password_enc, auth_type, environment, comments,
            owner_id, report_recipients, created_by, updated_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (data["name"], data["url"], data["email"], encrypt(data["password"]),
         data["auth_type"], data["environment"], data["comments"],
         owner_id, data.get("report_recipients", ""), editor, editor))
    db.commit()
    pid = cur.lastrowid
    db.close()
    return pid


def update_project(pid, data, editor="", encrypt=None, keep_password=None):
    db = get_connection()
    proj = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if data.get("password"):
        pw_enc = encrypt(data["password"])
    else:
        pw_enc = proj["password_enc"] if proj else ""
    db.execute(
        """UPDATE projects SET name=?, url=?, email=?, password_enc=?,
           auth_type=?, environment=?, comments=?, report_recipients=?,
           updated_by=?, updated_at=datetime('now') WHERE id=?""",
        (data["name"], data["url"], data["email"], pw_enc,
         data["auth_type"], data["environment"], data["comments"],
         data.get("report_recipients", ""), editor, pid))
    db.commit()
    db.close()


def toggle_project(pid):
    db = get_connection()
    row = db.execute("SELECT status FROM projects WHERE id=?", (pid,)).fetchone()
    if not row:
        db.close()
        return None
    new_status = "disabled" if row["status"] == "active" else "active"
    db.execute("UPDATE projects SET status=? WHERE id=?", (new_status, pid))
    db.commit()
    db.close()
    return new_status


def delete_project(pid):
    db = get_connection()
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Pages + actions (cartography)
# ---------------------------------------------------------------------------

def list_pages(pid):
    db = get_connection()
    rows = db.execute(
        "SELECT * FROM project_pages WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    db.close()
    return [ProjectPage.from_row(r) for r in rows]


def list_actions(pid):
    db = get_connection()
    rows = db.execute(
        "SELECT * FROM page_actions WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    db.close()
    return [PageAction.from_row(r) for r in rows]


def replace_pages_and_actions(pid, pages, actions):
    db = get_connection()
    db.execute("DELETE FROM project_pages WHERE project_id=?", (pid,))
    db.execute("DELETE FROM page_actions WHERE project_id=?", (pid,))
    for pg in pages:
        db.execute(
            "INSERT INTO project_pages (project_id, name, url, last_status) VALUES (?,?,?,?)",
            (pid, pg["name"], pg["url"], pg.get("status", 0)))
    for a in actions:
        db.execute(
            """INSERT INTO page_actions
               (project_id, page_url, page_name, action_type, label,
                action_key, x, y, el_index, sensitive)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (pid, a.get("page_url"), a.get("page_name"), a.get("action_type"),
             a.get("label"), a.get("action_key", ""), a.get("x", 0),
             a.get("y", 0), int(a.get("el_index") or 0), 1 if a.get("sensitive") else 0))
    db.commit()
    db.close()


def update_page_status(pid, url, status):
    db = get_connection()
    db.execute(
        "UPDATE project_pages SET last_status=? WHERE project_id=? AND url=?",
        (status, pid, url))
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Test runs + results
# ---------------------------------------------------------------------------

def create_test_run(pid, run_type, launched_by="", plan=None, owner_id=None):
    if owner_id is None:
        proj = get_project(pid)
        owner_id = (proj.row or {}).get("owner_id") if proj else None
    db = get_connection()
    cur = db.execute(
        "INSERT INTO test_runs (project_id, run_type, launched_by, plan_json, owner_id) VALUES (?,?,?,?,?)",
        (pid, run_type, launched_by, json.dumps(plan or [], ensure_ascii=False), owner_id))
    db.commit()
    tid = cur.lastrowid
    db.close()
    return tid


def get_test_run(tid, owner_id=None):
    db = get_connection()
    sql = """SELECT t.*, p.name as project_name, p.id as project_id, p.url as project_url,
           p.environment as project_env, p.report_recipients as report_recipients
           FROM test_runs t JOIN projects p ON t.project_id = p.id
           WHERE t.id=?"""
    args = [tid]
    if owner_id is not None:
        sql += (" AND (t.owner_id=? OR t.project_id IN "
                "(SELECT project_id FROM project_members WHERE user_id=?))")
        args += (owner_id,)
    row = db.execute(sql, args).fetchone()
    db.close()
    return TestRun.from_row(row) if row else None


def list_test_runs(pid=None, owner_id=None):
    db = get_connection()
    if pid:
        sql = """SELECT t.*, p.name as project_name FROM test_runs t
               JOIN projects p ON t.project_id=p.id
               WHERE t.project_id=?"""
        args = [pid]
        if owner_id is not None:
            sql += (" AND (t.owner_id=? OR t.project_id IN "
                    "(SELECT project_id FROM project_members WHERE user_id=?))")
            args += (owner_id, owner_id)
        sql += " ORDER BY t.started_at DESC"
        rows = db.execute(sql, args).fetchall()
    else:
        sql = """SELECT t.*, p.name as project_name FROM test_runs t
               JOIN projects p ON t.project_id=p.id"""
        args = []
        if owner_id is not None:
            sql += (" WHERE (t.owner_id=? OR t.project_id IN "
                    "(SELECT project_id FROM project_members WHERE user_id=?))")
            args += (owner_id, owner_id)
        sql += " ORDER BY t.started_at DESC"
        rows = db.execute(sql, args).fetchall()
    db.close()
    return [TestRun.from_row(r) for r in rows]


def list_results(tid):
    db = get_connection()
    rows = db.execute(
        "SELECT * FROM test_results WHERE test_id=? ORDER BY id", (tid,)).fetchall()
    db.close()
    return [TestResult.from_row(r) for r in rows]


def save_result(tid, res, screenshot="", http_status=0):
    db = get_connection()
    db.execute(
        """INSERT INTO test_results
           (test_id, module, function_name, action, data, status, severity,
            expected, obtained, screenshot, http_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, res["module"], res["function"], res.get("action", ""),
         res.get("data", ""), res["status"], res["severity"],
         res["expected"], res["obtained"], screenshot, http_status))
    db.execute(
        "UPDATE test_runs SET status='running' WHERE id=?", (tid,))
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Manual scenario runs (steps validated by the operator)
# ---------------------------------------------------------------------------

def mark_manual_step(rid, status):
    db = get_connection()
    db.execute("UPDATE test_results SET status=? WHERE id=?", (status, rid))
    db.commit()
    db.close()


def delete_test_run(tid):
    db = get_connection()
    db.execute("DELETE FROM test_runs WHERE id=?", (tid,))
    db.commit()
    db.close()


def finalize_manual_run(tid, running=False):
    """Mark a manual scenario run. When `running`, only signal it is in
    progress. Otherwise finalize with counters derived from validated steps."""
    db = get_connection()
    if running:
        db.execute("UPDATE test_runs SET status='running' WHERE id=?", (tid,))
    else:
        rows = db.execute(
            "SELECT status FROM test_results WHERE test_id=? AND status NOT IN ('', 'PENDING')",
            (tid,)).fetchall()
        total = passed = failed = warning = skipped = 0
        for r in rows:
            total += 1
            s = r["status"]
            if s == "PASS":
                passed += 1
            elif s == "FAIL":
                failed += 1
            elif s == "WARNING":
                warning += 1
            else:
                skipped += 1
        pending = db.execute(
            "SELECT COUNT(*) FROM test_results WHERE test_id=? AND status IN ('', 'PENDING')",
            (tid,)).fetchone()[0]
        total += pending
        skipped += pending
        db.execute(
            """UPDATE test_runs SET status='completed', finished_at=datetime('now'),
               total=?, passed=?, failed=?, warning=?, skipped=?, report_json='{}'
               WHERE id=?""",
            (total, passed, failed, warning, skipped, tid))
    db.commit()
    db.close()


def finalize_test_run(tid, status, counters, results):
    db = get_connection()
    db.execute(
        """UPDATE test_runs SET status=?, finished_at=datetime('now'),
           total=?, passed=?, failed=?, warning=?, skipped=?, report_json=?
           WHERE id=?""",
        (status, counters["total"], counters["passed"], counters["failed"],
         counters["warning"], counters["skipped"],
         json.dumps(results, ensure_ascii=False), tid))
    db.commit()
    db.close()


def save_test_error(tid, message):
    db = get_connection()
    db.execute(
        "UPDATE test_runs SET status='failed', finished_at=datetime('now'), report_json=? WHERE id=?",
        (json.dumps({"error": message}, ensure_ascii=False), tid))
    db.commit()
    db.close()


def test_status(tid):
    db = get_connection()
    row = db.execute("SELECT status FROM test_runs WHERE id=?", (tid,)).fetchone()
    db.close()
    return row["status"] if row else None


def run_progress(tid):
    """Return (done, total, percent) for a running automated test run.

    total is derived from the stored plan (plan_json) when available;
    otherwise from the results already recorded.
    """
    db = get_connection()
    row = db.execute("SELECT plan_json FROM test_runs WHERE id=?", (tid,)).fetchone()
    done = db.execute(
        "SELECT COUNT(*) FROM test_results WHERE test_id=?", (tid,)).fetchone()[0]
    db.close()
    total = 0
    if row and row["plan_json"]:
        try:
            total = len(json.loads(row["plan_json"]))
        except Exception:
            total = 0
    if total <= 0:
        total = max(done, 1)
    pct = min(round((done / total) * 100), 100) if total > 0 else 0
    return done, total, pct


def request_cancel(tid):
    db = get_connection()
    db.execute(
        "UPDATE test_runs SET status='cancel_requested' WHERE id=? AND status IN ('running','exploring','waiting_otp','otp_submitted')",
        (tid,))
    db.commit()
    db.close()


def submit_otp(tid, code):
    db = get_connection()
    db.execute("UPDATE test_runs SET status='otp_submitted', otp_code=? WHERE id=?",
               (code, tid))
    db.commit()
    db.close()


def set_waiting_otp(tid):
    db = get_connection()
    db.execute("UPDATE test_runs SET status='waiting_otp' WHERE id=?", (tid,))
    db.commit()
    db.close()


def set_running_total(tid, total):
    db = get_connection()
    db.execute("UPDATE test_runs SET status='exploring', total=? WHERE id=?", (total, tid))
    db.commit()
    db.close()


def mark_explored(tid, total):
    db = get_connection()
    db.execute(
        "UPDATE test_runs SET status='explored', finished_at=datetime('now'), total=? WHERE id=?",
        (total, tid))
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Settings (Paramètres UI) - persisted runtime configuration
# ---------------------------------------------------------------------------

def load_settings():
    db = get_connection()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    db.close()
    return {r["key"]: r["value"] for r in rows}


def save_settings(values):
    data = {str(k): str(v) for k, v in values.items() if v is not None}
    db = get_connection()
    for key, value in data.items():
        db.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
               updated_at=datetime('now')""",
            (key, value))
    db.commit()
    db.close()


def delete_settings(keys):
    if not keys:
        return
    db = get_connection()
    db.executemany("DELETE FROM settings WHERE key=?", [(k,) for k in keys])
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Dashboard metrics + history
# ---------------------------------------------------------------------------

def dashboard_metrics(owner_id=None):
    db = get_connection()

    def one(sql, args=()):
        return db.execute(sql, args).fetchone()[0]

    if owner_id is not None:
        total_projects = one(
            "SELECT COUNT(*) FROM projects WHERE owner_id=? OR id IN "
            "(SELECT project_id FROM project_members WHERE user_id=?)",
            (owner_id, owner_id))
        active_projects = one(
            "SELECT COUNT(*) FROM projects WHERE status='active' AND "
            "(owner_id=? OR id IN (SELECT project_id FROM project_members WHERE user_id=?))",
            (owner_id, owner_id))
        total_tests = one(
            "SELECT COUNT(*) FROM test_runs WHERE status='completed' AND "
            "(owner_id=? OR project_id IN (SELECT project_id FROM project_members WHERE user_id=?))",
            (owner_id, owner_id))
    else:
        total_projects = one("SELECT COUNT(*) FROM projects")
        active_projects = one("SELECT COUNT(*) FROM projects WHERE status='active'")
        total_tests = one("SELECT COUNT(*) FROM test_runs WHERE status='completed'")

    owner_clause = (" AND (owner_id=? OR id IN (SELECT project_id FROM "
                    "project_members WHERE user_id=?))" if owner_id is not None else "")
    owner_run_clause = (" AND (t.owner_id=? OR t.project_id IN (SELECT project_id FROM "
                        "project_members WHERE user_id=?))" if owner_id is not None else "")
    owner_args = (owner_id, owner_id) if owner_id is not None else ()
    total_passed = one(f"SELECT COALESCE(SUM(passed),0) FROM test_runs t WHERE t.status='completed'{owner_run_clause}", owner_args)
    total_failed = one(f"SELECT COALESCE(SUM(failed),0) FROM test_runs t WHERE t.status='completed'{owner_run_clause}", owner_args)
    total_warning = one(f"SELECT COALESCE(SUM(warning),0) FROM test_runs t WHERE t.status='completed'{owner_run_clause}", owner_args)
    total_skipped = one(f"SELECT COALESCE(SUM(skipped),0) FROM test_runs t WHERE t.status='completed'{owner_run_clause}", owner_args)
    total_all = total_passed + total_failed + total_warning + total_skipped
    success_rate = round((total_passed / total_all) * 100) if total_all > 0 else 0

    recent = db.execute(
        """SELECT t.*, p.name as project_name FROM test_runs t
           JOIN projects p ON t.project_id=p.id
           {owner} ORDER BY t.started_at DESC LIMIT 3""".format(
            owner="WHERE " + owner_run_clause[5:] if owner_id is not None else ""),
        owner_args if owner_id is not None else ()).fetchall()

    per_project = db.execute(
        """SELECT p.name as name,
                  COUNT(t.id) as runs,
                  COALESCE(SUM(t.passed),0) as passed,
                  COALESCE(SUM(t.failed),0) as failed,
                  COALESCE(SUM(t.warning),0) as warning,
                  COALESCE(SUM(t.skipped),0) as skipped
           FROM projects p
           LEFT JOIN test_runs t ON t.project_id=p.id AND t.status='completed'
           {owner}
           GROUP BY p.id
           ORDER BY p.name""".format(
            owner="WHERE (p.owner_id=? OR p.id IN (SELECT project_id FROM "
                  "project_members WHERE user_id=?))" if owner_id is not None else ""),
        owner_args if owner_id is not None else ()).fetchall()
    db.close()

    pp_list = []
    for p in per_project:
        total = p["passed"] + p["failed"] + p["warning"] + p["skipped"]
        pp_list.append({
            "name": p["name"],
            "runs": p["runs"],
            "passed": p["passed"],
            "failed": p["failed"],
            "warning": p["warning"],
            "skipped": p["skipped"],
            "total": total,
            "success_rate": round((p["passed"] / total) * 100) if total > 0 else 0,
        })

    return {
        "total_projects": total_projects,
        "active_projects": active_projects,
        "total_tests": total_tests,
        "total_passed": total_passed,
        "total_failed": total_failed,
        "total_warning": total_warning,
        "total_skipped": total_skipped,
        "success_rate": success_rate,
        "recent": [dict(r) for r in recent],
        "per_project": pp_list,
    }
