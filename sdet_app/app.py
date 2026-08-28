"""Flask application entry point.

Only responsibilities: routes, sessions, pages rendering, and starting
explorations/tests in background threads. All SDET logic lives in sdet/.
"""
import json
import threading

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify)

from .config import env, get_setting, apply_settings
from . import database, security
from .sdet import planner, engine

app = Flask(__name__, static_folder=env.STATIC_DIR, template_folder=env.TEMPLATE_DIR)
app.secret_key = env.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


def login_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = database.get_user_by_credentials(
            request.form.get("email", "").strip(),
            request.form.get("password", ""))
        if user:
            session["user_id"] = user.id
            session["user_email"] = user.email
            return redirect(url_for("dashboard"))
        flash("Identifiants incorrects", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    metrics = database.dashboard_metrics()
    return render_template("dashboard.html", m=metrics)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.route("/projects")
@login_required
def projects():
    proj_list = database.list_projects()
    return render_template("projects.html", projects=proj_list)


@app.route("/projects/new", methods=["GET", "POST"])
@login_required
def project_new():
    if request.method == "POST":
        data = _project_form()
        errors = _validate_project(data)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("project_form.html", project=None, editing=False)
        database.create_project(data, session.get("user_email", ""),
                                encrypt=security.encrypt_value)
        flash("Projet créé avec succès", "success")
        return redirect(url_for("projects"))
    return render_template("project_form.html", project=None, editing=False)


@app.route("/projects/<int:pid>")
@login_required
def project_detail(pid):
    project = database.get_project(pid)
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))
    tests = database.list_test_runs(pid)
    pages = database.list_pages(pid)
    latest = _latest_completed(pid)
    success_pct = _success_pct(latest)
    return render_template("project_detail.html", project=project,
                           tests=tests, pages=pages, success_pct=success_pct,
                           latest=latest, pages_count=len(pages))


@app.route("/projects/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def project_edit(pid):
    project = database.get_project(pid)
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))
    if request.method == "POST":
        data = _project_form()
        errors = _validate_project(data, editing=True)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("project_form.html", project=project, editing=True)
        database.update_project(pid, data, session.get("user_email", ""),
                                encrypt=security.encrypt_value)
        flash("Projet mis à jour", "success")
        return redirect(url_for("project_detail", pid=pid))
    return render_template("project_form.html", project=project, editing=True)


@app.route("/projects/<int:pid>/toggle", methods=["POST"])
@login_required
def project_toggle(pid):
    status = database.toggle_project(pid)
    if not status:
        flash("Projet introuvable", "error")
    else:
        flash("Projet %s" % ("activé" if status == "active" else "désactivé"), "success")
    return redirect(url_for("projects"))


@app.route("/projects/<int:pid>/delete", methods=["POST"])
@login_required
def project_delete(pid):
    database.delete_project(pid)
    flash("Projet supprimé", "success")
    return redirect(url_for("projects"))


# ---------------------------------------------------------------------------
# Exploration
# ---------------------------------------------------------------------------

@app.route("/projects/<int:pid>/explore", methods=["POST"])
@login_required
def project_explore(pid):
    project = database.get_project(pid)
    if not project or project["status"] != "active":
        flash("Projet introuvable ou désactivé", "error")
        return redirect(url_for("project_detail", pid=pid))
    tid = database.create_test_run(pid, "Exploration", session.get("user_email", ""))
    threading.Thread(target=engine.run_exploration, args=(tid, pid), daemon=True).start()
    return redirect(url_for("test_view", tid=tid))


# ---------------------------------------------------------------------------
# Module selection + planning
# ---------------------------------------------------------------------------

@app.route("/projects/<int:pid>/select")
@login_required
def project_select(pid):
    project = database.get_project(pid)
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))
    pages = database.list_pages(pid)
    actions = database.list_actions(pid)
    page_groups = _group_actions(pages, actions, project["environment"])
    return render_template("project_select.html", project=project,
                           page_groups=page_groups)


@app.route("/projects/<int:pid>/cartography")
@login_required
def project_cartography(pid):
    project = database.get_project(pid)
    pages = database.list_pages(pid)
    actions = database.list_actions(pid)
    by_url = {}
    for pg in pages:
        by_url[pg.url] = {"page": pg, "actions": []}
    for a in actions:
        by_url.setdefault(a.page_url, {"page": None, "actions": []})["actions"].append(a)
    return render_template("cartography.html", project=project, by_url=by_url)


@app.route("/projects/<int:pid>/run", methods=["POST"])
@login_required
def test_run(pid):
    project = database.get_project(pid)
    if not project or project["status"] != "active":
        flash("Projet introuvable ou désactivé", "error")
        return redirect(url_for("projects"))

    action_ids = request.form.getlist("action_ids")
    if not action_ids:
        flash("Sélectionnez au moins une action à tester", "error")
        return redirect(url_for("project_select", pid=pid))

    selected = _fetch_selected_actions(pid, action_ids)
    if not selected:
        flash("Veuillez d'abord explorer l'application", "error")
        return redirect(url_for("project_detail", pid=pid))

    pages = database.list_pages(pid)
    help_ai = _ai_help()
    plan = planner.build_scenarios(pages, [], selected,
                                   environment=project["environment"] or "STAGING",
                                   help_ai=help_ai)

    tid = database.create_test_run(pid, "Test", session.get("user_email", ""), plan=plan)
    threading.Thread(target=engine.run_test, args=(tid, pid, plan), daemon=True).start()
    return redirect(url_for("test_view", tid=tid))


@app.route("/projects/<int:pid>/regression", methods=["POST"])
@login_required
def run_regression(pid):
    project = database.get_project(pid)
    if not project or project["status"] != "active":
        flash("Projet introuvable ou désactivé", "error")
        return redirect(url_for("projects"))
    pages = database.list_pages(pid)
    if not pages:
        flash("Aucune exploration disponible pour la régression", "error")
        return redirect(url_for("project_detail", pid=pid))

    actions = database.list_actions(pid)
    safe = [a for a in actions if not a.sensitive]

    help_ai = _ai_help()
    plan = planner.build_scenarios(pages, [], [a.__dict__ for a in safe],
                                   environment=project["environment"] or "STAGING",
                                   help_ai=help_ai)
    tid = database.create_test_run(pid, "Régression", session.get("user_email", ""), plan=plan)
    threading.Thread(target=engine.run_test, args=(tid, pid, plan), daemon=True).start()
    return redirect(url_for("test_view", tid=tid))


# ---------------------------------------------------------------------------
# Settings (Paramètres)
# ---------------------------------------------------------------------------

SETTINGS_FIELDS = [
    ("max_pages", "Pages maximum", "int",
     "Limite de pages parcourues lors d'une exploration."),
    ("max_buttons", "Boutons / actions par page", "int",
     "Nombre maximum d'éléments interactifs inventoriés par page."),
    ("max_forms", "Formulaires par page", "int",
     "Nombre maximum de formulaires détectés par page."),
    ("page_timeout", "Délai de chargement (ms)", "int",
     "Temps maximal d'attente du chargement d'une page."),
    ("headless", "Navigateur sans interface", "bool",
     "Lance le navigateur en arrière-plan (recommandé sur un serveur)."),
]

SETTINGS_DEFAULTS = {
    "max_pages": env.MAX_PAGES,
    "max_buttons": 300,
    "max_forms": 30,
    "page_timeout": env.PAGE_TIMEOUT,
    "headless": env.HEADLESS,
}


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        payload = {}
        for key, _label, ftype, _help in SETTINGS_FIELDS:
            raw = (request.form.get(key) or "").strip()
            if ftype == "bool":
                payload[key] = "1" if raw in ("1", "true", "on", "oui") else "0"
            elif raw:
                payload[key] = raw
        apply_settings(payload)
        flash("Paramètres enregistrés", "success")
        return redirect(url_for("settings"))

    vals = {k: get_setting(k, d) for k, d in SETTINGS_DEFAULTS.items()}
    vals["headless"] = "1" if str(vals["headless"]).lower() in ("1", "true", "yes", "on") else "0"
    return render_template("settings.html", fields=SETTINGS_FIELDS, vals=vals)


# ---------------------------------------------------------------------------
# Test view / OTP / cancel / results
# ---------------------------------------------------------------------------

@app.route("/tests/<int:tid>")
@login_required
def test_view(tid):
    test = database.get_test_run(tid)
    if not test:
        flash("Test introuvable", "error")
        return redirect(url_for("projects"))
    results = database.list_results(tid)
    template = "exploration.html" if getattr(test, "run_type", "") == "Exploration" else "test_run.html"
    return render_template(template, test=test, results=results)


@app.route("/tests/<int:tid>/otp", methods=["POST"])
@login_required
def test_otp(tid):
    code = request.form.get("otp_code", "").strip()
    if not code:
        flash("Veuillez saisir le code OTP", "error")
        return redirect(url_for("test_view", tid=tid))
    database.submit_otp(tid, code)
    flash("Code OTP envoyé", "success")
    return redirect(url_for("test_view", tid=tid))


@app.route("/tests/<int:tid>/cancel", methods=["POST"])
@login_required
def test_cancel(tid):
    database.request_cancel(tid)
    flash("Arrêt du test demandé...", "info")
    return redirect(url_for("test_view", tid=tid))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@app.route("/reports")
@login_required
def reports():
    tests = [t for t in database.list_test_runs()
             if t.status in ("completed", "failed")]
    return render_template("reports.html", tests=tests)


@app.route("/reports/<int:tid>")
@login_required
def report_detail(tid):
    test = database.get_test_run(tid)
    if not test:
        flash("Rapport introuvable", "error")
        return redirect(url_for("reports"))
    results = database.list_results(tid)
    from .sdet import reporter as rep
    counters = rep.counters_from_results([r.__dict__ for r in results])
    report = rep.build_report({"name": getattr(test, "project_name", ""),
                               "environment": getattr(test, "project_env", "")},
                              {"started_at": test.started_at, "launched_by": test.launched_by,
                               "run_type": test.run_type, "id": test.id},
                              [r.__dict__ for r in results], counters, 0)
    return render_template("report.html", test=test, results=results,
                           report=report)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_form():
    return {
        "name": request.form.get("name", "").strip(),
        "url": request.form.get("url", "").strip(),
        "email": request.form.get("email", "").strip(),
        "password": request.form.get("password", ""),
        "auth_type": request.form.get("auth_type", "simple"),
        "environment": request.form.get("environment", "STAGING"),
        "comments": request.form.get("comments", "").strip(),
    }


def _validate_project(data, editing=False):
    errors = []
    if not data["name"]:
        errors.append("Le nom du projet est obligatoire")
    if not data["url"] or not data["url"].startswith("http"):
        errors.append("Une URL valide (http/https) est obligatoire")
    if data["auth_type"] not in ("none", "simple", "2fa"):
        errors.append("Type d'authentification invalide")
    if data["environment"] not in ("DEV", "TEST", "STAGING", "PRODUCTION"):
        errors.append("Environnement invalide")
    if not editing and not data["password"] and data["auth_type"] != "none":
        errors.append("Le mot de passe est requis pour cette authentification")
    return errors


def _group_actions(pages, actions, environment):
    by_url = {}
    for pg in pages:
        by_url[pg.url] = {"page": pg, "actions": []}
    for a in actions:
        by_url.setdefault(a.page_url, {"page": None, "actions": []})["actions"].append(a)
    groups = []
    for url, g in by_url.items():
        page = g["page"]
        avail = planner.applicable_atomic_actions(
            [dict(id=a.id, label=a.label, action_type=a.action_type,
                  page_url=a.page_url, page_name=a.page_name,
                  el_index=getattr(a, "el_index", 0),
                  action_key=getattr(a, "action_key", ""))
             for a in g["actions"]], environment)
        groups.append({
            "url": url,
            "name": page.name if page else (g["actions"][0].page_name if g["actions"] else "Page"),
            "status": page.last_status if page else 0,
            "actions": avail,
        })
    return groups


def _fetch_selected_actions(pid, ids):
    ids = [int(i) for i in ids]
    actions = database.list_actions(pid)
    selected = []
    for a in actions:
        if a.id in ids:
            selected.append(a.__dict__)
    return selected


def _latest_completed(pid):
    for t in database.list_test_runs(pid):
        if t.status == "completed":
            return t
    return None


def _success_pct(latest):
    if not latest or not (latest.total or 0):
        return 0
    return round((latest.passed / latest.total) * 100)


def _ai_help():
    if not env.OPENAI_API_KEY:
        return None
    from .sdet.ai import classifies_action
    return classifies_action
