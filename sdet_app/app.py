"""Flask application entry point.

Only responsibilities: routes, sessions, pages rendering, and starting
explorations/tests in background threads. All SDET logic lives in sdet/.
"""
import json
import re
import threading
import time

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify)

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify)

from .config import env, get_setting, apply_settings
from . import database, security
from .sdet import planner, engine, generator

app = Flask(__name__, static_folder=env.STATIC_DIR, template_folder=env.TEMPLATE_DIR)
app.secret_key = env.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# Background step-generation tracking (in-memory, single local user)
# ---------------------------------------------------------------------------
GENERATE_TIMEOUT = 180  # seconds before the generation is reported as timed out

_generate_lock = threading.Lock()
_generations = {}  # fid -> {status, steps, error, started, finished}


def _prune_generations(now):
    for fid in list(_generations):
        g = _generations[fid]
        if g["status"] != "running" and now - g.get("finished", 0) > 120:
            del _generations[fid]


# Whole-app / module scans (single background pass upstream of generation)
_scan_lock = threading.Lock()
_scans = {}  # ("module", mid) | ("app", pid) -> {status, message, started, finished}


def _scan_age(key):
    g = _scans.get(key)
    if not g:
        return 0
    return time.time() - g.get("started", 0)


def _set_scan(key, **fields):
    with _scan_lock:
        g = _scans.setdefault(key, {})
        g.update(fields)


def _get_scan(key):
    with _scan_lock:
        return dict(_scans.get(key) or {})


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


@app.route("/projects/<int:pid>/run_all", methods=["POST"])
@login_required
def test_run_all(pid):
    """Lancer les tests directement sur tout ce qui a été scanné."""
    project = database.get_project(pid)
    if not project or project["status"] != "active":
        flash("Projet introuvable ou désactivé", "error")
        return redirect(url_for("projects"))
    pages = database.list_pages(pid)
    if not pages:
        flash("Aucune exploration disponible. Explorez d'abord l'application.", "error")
        return redirect(url_for("project_detail", pid=pid))

    actions = database.list_actions(pid)
    safe = [a for a in actions if not a.sensitive]

    help_ai = _ai_help()
    plan = planner.build_scenarios(pages, [], [a.__dict__ for a in safe],
                                   environment=project["environment"] or "STAGING",
                                   help_ai=help_ai)
    tid = database.create_test_run(pid, "Test complet", session.get("user_email", ""), plan=plan)
    threading.Thread(target=engine.run_test, args=(tid, pid, plan), daemon=True).start()
    return redirect(url_for("test_view", tid=tid))


@app.route("/projects/<int:pid>/walk", methods=["POST"])
@login_required
def run_walk(pid):
    """Parcourir tous les boutons : re-scanner chaque page et cliquer chaque
    bouton physique visible (mode 'trouver + parcourir tous les boutons')."""
    project = database.get_project(pid)
    if not project or project["status"] != "active":
        flash("Projet introuvable ou désactivé", "error")
        return redirect(url_for("projects"))
    pages = database.list_pages(pid)
    if not pages:
        flash("Aucune exploration disponible. Explorez d'abord l'application.", "error")
        return redirect(url_for("project_detail", pid=pid))

    plan = planner.build_walk(pages)
    tid = database.create_test_run(pid, "Parcours boutons", session.get("user_email", ""), plan=plan)
    threading.Thread(target=engine.run_test, args=(tid, pid, plan), daemon=True).start()
    return redirect(url_for("test_view", tid=tid))


# ---------------------------------------------------------------------------
# Scénarios de test manuels (modules -> fonctionnalités -> étapes)
# ---------------------------------------------------------------------------

@app.route("/projects/<int:pid>/scenarios")
@login_required
def project_scenarios(pid):
    project = database.get_project(pid)
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))
    tree = database.scenario_tree(pid)
    return render_template("scenarios.html", project=project, tree=tree)


@app.route("/projects/<int:pid>/scenarios/modules", methods=["POST"])
@login_required
def scenario_module_create(pid):
    name = request.form.get("name", "").strip()
    desc = request.form.get("description", "").strip()
    url = request.form.get("url", "").strip()
    if not name:
        flash("Le nom du module est obligatoire", "error")
        return redirect(url_for("project_scenarios", pid=pid))
    database.create_module(pid, name, desc, url)
    flash("Module créé", "success")
    return redirect(url_for("project_scenarios", pid=pid))


@app.route("/projects/<int:pid>/scenarios/modules/<int:mid>/delete", methods=["POST"])
@login_required
def scenario_module_delete(pid, mid):
    database.delete_module(mid)
    flash("Module supprimé", "success")
    return redirect(url_for("project_scenarios", pid=pid))


@app.route("/projects/<int:pid>/scenarios/modules/<int:mid>/edit", methods=["GET", "POST"])
@login_required
def scenario_module_edit(pid, mid):
    project = database.get_project(pid)
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))
    module = database.get_module(mid)
    if not module:
        flash("Module introuvable", "error")
        return redirect(url_for("project_scenarios", pid=pid))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        url = request.form.get("url", "").strip()
        if not name:
            flash("Le nom du module est obligatoire", "error")
            return render_template("module_edit.html", project=project,
                                   module=module)
        database.update_module(mid, name, description, url)
        flash("Module modifié", "success")
        return redirect(url_for("project_scenarios", pid=pid))
    return render_template("module_edit.html", project=project, module=module)


@app.route("/projects/<int:pid>/scenarios/modules/<int:mid>/functionalities", methods=["POST"])
@login_required
def scenario_functionality_create(pid, mid):
    name = request.form.get("name", "").strip()
    desc = request.form.get("description", "").strip()
    path = request.form.get("path", "").strip()
    url = request.form.get("url", "").strip()
    if not name:
        flash("Le nom de la fonctionnalité est obligatoire", "error")
        return redirect(url_for("project_scenarios", pid=pid))
    database.create_functionality(mid, name, desc, path, url)
    flash("Fonctionnalité créée", "success")
    return redirect(url_for("project_scenarios", pid=pid))


@app.route("/projects/<int:pid>/scenarios/functionalities/<int:fid>/delete", methods=["POST"])
@login_required
def scenario_functionality_delete(pid, fid):
    database.delete_functionality(fid)
    flash("Fonctionnalité supprimée", "success")
    return redirect(url_for("project_scenarios", pid=pid))


@app.route("/projects/<int:pid>/scenarios/functionalities/<int:fid>/toggle", methods=["POST"])
@login_required
def scenario_functionality_toggle(pid, fid):
    """Toggle active/inactive state for a functionality."""
    new_state = database.toggle_functionality(fid)
    if new_state is None:
        flash("Fonctionnalité introuvable", "error")
    else:
        state_text = "activé" if new_state else "désactivé"
        flash(f"Fonctionnalité {state_text}", "success")
    return redirect(url_for("project_scenarios", pid=pid))


@app.route("/projects/<int:pid>/scenarios/functionalities/<int:fid>/steps", methods=["POST"])
@login_required
def scenario_step_create(pid, fid):
    description = request.form.get("description", "").strip()
    if not description:
        flash("Le libellé de l'étape est obligatoire", "error")
        return redirect(url_for("project_scenarios", pid=pid))
    database.add_step(fid, description)
    flash("Étape ajoutée", "success")
    return redirect(url_for("project_scenarios", pid=pid))


@app.route("/projects/<int:pid>/scenarios/functionalities/<int:fid>/generate", methods=["POST"])
@login_required
def scenario_functionality_generate(pid, fid):
    """Option C: generate the create-then-verify scenario from the live
    screen. Runs in a background thread (with a timeout enforced by the
    status endpoint) and returns JSON so the frontend can show a loading
    indicator. Replaces the existing steps so the button is repeatable."""
    project_obj = database.get_project(pid)
    if not project_obj:
        return jsonify({"status": "error", "message": "Projet introuvable"}), 404
    project = dict(project_obj.row)
    fn = database.get_functionality(fid)
    if not fn:
        return jsonify({"status": "error", "message": "Fonctionnalité introuvable"}), 404
    module = database.get_module(fn["module_id"])
    module_name = module["name"] if module else "Général"
    folder_path = _parse_path(fn.get("path") or "")

    with _generate_lock:
        _prune_generations(time.time())
        if _generations.get(fid, {}).get("status") == "running":
            return jsonify({"status": "running"}), 409

        for s in database.list_steps(fid):
            database.delete_step(s["id"])

        _generations[fid] = {"status": "running", "steps": [],
                             "error": None, "started": time.time(),
                             "finished": None}

    def _worker():
        try:
            steps, err = generator.generate_steps(
                project, nav_path=folder_path, module=module_name,
                functionality=fn["name"], url=fn.get("url", ""))
        except Exception as e:
            steps, err = [], f"Échec de la génération : {e}"

        with _generate_lock:
            if err:
                _generations[fid]["status"] = "error"
                _generations[fid]["error"] = err
            else:
                for s in steps:
                    database.add_step(fid, s)
                _generations[fid]["status"] = "done"
                _generations[fid]["steps"] = steps
            _generations[fid]["finished"] = time.time()

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/projects/<int:pid>/scenarios/functionalities/<int:fid>/generate/status")
@login_required
def scenario_functionality_generate_status(pid, fid):
    """JSON status of a background step generation, enforcing a timeout."""
    with _generate_lock:
        g = _generations.get(fid)
        if g is None:
            return jsonify({"status": "idle"})
        if g["status"] == "running":
            elapsed = time.time() - g.get("started", time.time())
            if elapsed > GENERATE_TIMEOUT:
                g["status"] = "error"
                g["error"] = (f"La génération a dépassé le délai de "
                              f"{GENERATE_TIMEOUT} secondes et a été arrêtée.")
                g["finished"] = time.time()
                return jsonify({"status": "error", "message": g["error"]})
            return jsonify({"status": "running", "elapsed": round(elapsed)})
        return jsonify({
            "status": g["status"],
            "message": g.get("error"),
            "count": len(g.get("steps") or []),
        })


# ---------------------------------------------------------------------------
# Module & whole-app scan (background, single browser pass)
# ---------------------------------------------------------------------------

@app.route("/projects/<int:pid>/scenarios/modules/<int:mid>/generate", methods=["POST"])
@login_required
def scenario_module_generate(pid, mid):
    """Scan a single module: auto-discover its functionalities and generate
    their CRUD scenarios in one pass. Replaces the module's functionalities."""
    project_obj = database.get_project(pid)
    if not project_obj:
        return jsonify({"status": "error", "message": "Projet introuvable"}), 404
    project = dict(project_obj.row)
    module = database.get_module(mid)
    if not module:
        return jsonify({"status": "error", "message": "Module introuvable"}), 404
    if project["status"] != "active":
        return jsonify({"status": "error",
                        "message": "Projet désactivé — activez-le d'abord."}), 400

    key = ("module", mid)
    if _get_scan(key).get("status") == "running":
        return jsonify({"status": "running"}), 409

    _set_scan(key, status="running", message=None, started=time.time(),
              finished=None)

    def _worker():
        try:
            results, err = generator.scan_module(project, module)
            if not err and results:
                mod_url = module.get("url") or ""
                if mod_url:
                    database.update_module(mid, module.get("name", ""),
                                           module.get("description", ""),
                                           mod_url)
        except Exception as e:
            results, err = [], f"Échec du scan du module : {e}"
        if err:
            _set_scan(key, status="error", message=err, finished=time.time())
            return
        try:
            for fn in database.list_functionalities(mid):
                database.delete_functionality(fn["id"])
            for r in results:
                fid = database.create_functionality(
                    mid, r["functionality"], description="", path=r.get("path", ""),
                    url=r.get("url", ""))
                for s in r["steps"]:
                    database.add_step(fid, s)
            fn_count = len(results)
            _set_scan(key, status="done", message=None,
                      count=fn_count, finished=time.time())
        except Exception as e:
            _set_scan(key, status="error",
                      message=f"Erreur pendant l'enregistrement : {e}",
                      finished=time.time())

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/projects/<int:pid>/scenarios/modules/<int:mid>/generate/status")
@login_required
def scenario_module_generate_status(pid, mid):
    key = ("module", mid)
    g = _get_scan(key)
    if not g:
        return jsonify({"status": "idle"})
    if g["status"] == "running":
        if time.time() - g.get("started", 0) > GENERATE_TIMEOUT:
            _set_scan(key, status="error",
                      message=f"Le scan a dépassé le délai de {GENERATE_TIMEOUT} secondes.",
                      finished=time.time())
            return jsonify({"status": "error",
                            "message": _get_scan(key).get("message")})
        return jsonify({"status": "running",
                        "elapsed": round(time.time() - g.get("started", 0))})
    return jsonify({"status": g["status"], "message": g.get("message"),
                    "count": g.get("count", 0)})


@app.route("/projects/<int:pid>/scenarios/scan", methods=["POST"])
@login_required
def scenario_scan_all(pid):
    """Scan the whole application without any user-provided module: detect
    modules from the top-level navigation, discover their functionalities and
    write the CRUD scenarios — all in a single browser pass. Replaces every
    existing module for the project."""
    project_obj = database.get_project(pid)
    if not project_obj:
        return jsonify({"status": "error", "message": "Projet introuvable"}), 404
    project = dict(project_obj.row)
    if project["status"] != "active":
        return jsonify({"status": "error",
                        "message": "Projet désactivé — activez-le d'abord."}), 400

    key = ("app", pid)
    if _get_scan(key).get("status") == "running":
        return jsonify({"status": "running"}), 409

    _set_scan(key, status="running", message=None, started=time.time(),
              finished=None)

    def _worker():
        try:
            modules, err = generator.scan_whole_app(project)
        except Exception as e:
            modules, err = [], f"Échec du scan de l'application : {e}"
        if err:
            _set_scan(key, status="error", message=err, finished=time.time())
            return
        try:
            for m in database.list_modules(pid):
                database.delete_module(m["id"])
            for m in modules:
                mid = database.create_module(pid, m["module"], "",
                                             m.get("url", ""))
                for fn in m["functionalities"]:
                    fid = database.create_functionality(
                        mid, fn["functionality"], description="",
                        path=fn.get("path", ""), url=fn.get("url", ""))
                    for s in fn["steps"]:
                        database.add_step(fid, s)
            total = sum(len(m["functionalities"]) for m in modules)
            _set_scan(key, status="done", message=None,
                      count=total, finished=time.time())
        except Exception as e:
            _set_scan(key, status="error",
                      message=f"Erreur pendant l'enregistrement : {e}",
                      finished=time.time())

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/projects/<int:pid>/scenarios/scan/status")
@login_required
def scenario_scan_all_status(pid):
    key = ("app", pid)
    g = _get_scan(key)
    if not g:
        return jsonify({"status": "idle"})
    if g["status"] == "running":
        if time.time() - g.get("started", 0) > GENERATE_TIMEOUT:
            _set_scan(key, status="error",
                      message=f"Le scan a dépassé le délai de {GENERATE_TIMEOUT} secondes.",
                      finished=time.time())
            return jsonify({"status": "error",
                            "message": _get_scan(key).get("message")})
        return jsonify({"status": "running",
                        "elapsed": round(time.time() - g.get("started", 0))})
    return jsonify({"status": g["status"], "message": g.get("message"),
                    "count": g.get("count", 0)})


@app.route("/projects/<int:pid>/scenarios/steps/<int:sid>/delete", methods=["POST"])
@login_required
def scenario_step_delete(pid, sid):
    database.delete_step(sid)
    flash("Étape supprimée", "success")
    return redirect(url_for("project_scenarios", pid=pid))


@app.route("/projects/<int:pid>/scenarios/functionalities/<int:fid>/edit", methods=["GET", "POST"])
@login_required
def scenario_functionality_edit(pid, fid):
    project = database.get_project(pid)
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))
    func = database.get_functionality(fid)
    if not func:
        flash("Fonctionnalité introuvable", "error")
        return redirect(url_for("project_scenarios", pid=pid))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        desc = request.form.get("description", "").strip()
        path = request.form.get("path", "").strip()
        url = request.form.get("url", "").strip()
        if not name:
            flash("Le nom est obligatoire", "error")
            return redirect(url_for("scenario_functionality_edit", pid=pid, fid=fid))
        database.update_functionality(fid, name, desc, path, url)
        flash("Fonctionnalité mise à jour", "success")
        return redirect(url_for("project_scenarios", pid=pid))
    return render_template("scenario_edit.html", project=project, func=func, kind="functionality")


@app.route("/projects/<int:pid>/scenarios/steps/<int:sid>/edit", methods=["GET", "POST"])
@login_required
def scenario_step_edit(pid, sid):
    project = database.get_project(pid)
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))
    step = database.get_step(sid)
    if not step:
        flash("Étape introuvable", "error")
        return redirect(url_for("project_scenarios", pid=pid))
    if request.method == "POST":
        description = request.form.get("description", "").strip()
        if not description:
            flash("La description est obligatoire", "error")
            return redirect(url_for("scenario_step_edit", pid=pid, sid=sid))
        database.update_step(sid, description)
        flash("Étape mise à jour", "success")
        return redirect(url_for("project_scenarios", pid=pid))
    func = database.get_functionality(step["functionality_id"])
    return render_template("scenario_edit.html", project=project, step=step, func=func, kind="step")


@app.route("/projects/<int:pid>/scenarios/select")
@login_required
def scenario_select(pid):
    project = database.get_project(pid)
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))
    tree = database.scenario_tree(pid)
    return render_template("scenario_select.html", project=project, tree=tree)


@app.route("/projects/<int:pid>/scenarios/run", methods=["POST"])
@login_required
def scenario_run(pid):
    project = database.get_project(pid)
    if not project:
        flash("Projet introuvable", "error")
        return redirect(url_for("projects"))

    # selected functionality ids are completely independent of exploration
    fids = request.form.getlist("functionality_ids")
    # Filter out disabled functionalities
    active_fids = []
    for fid in fids:
        fn = database.get_functionality(int(fid)) if fid.isdigit() else None
        if fn and fn.get("is_active", 1):
            active_fids.append(fid)
    fids = active_fids
    plan, count = _build_ai_plan(pid, fids)
    if not plan:
        flash("Sélectionnez au moins une fonctionnalité à tester", "error")
        return redirect(url_for("scenario_select", pid=pid))

    # Alerter si des fonctionnalités n'ont ni étapes ni description
    incomplete = []
    for s in plan:
        if not s.get("steps") and not s.get("function_description", "").strip():
            incomplete.append(s["function"])
    if incomplete:
        names = ", ".join(incomplete[:5])
        flash(
            f"Attention : {len(incomplete)} fonctionnalité(s) n'ont ni étapes "
            f"ni description ({names}). L'IA va essayer de naviguer vers la "
            f"fonctionnalité mais sans instruction précise.",
            "warning")

    tid = database.create_test_run(pid, "Scénario", session.get("user_email", ""),
                                   plan=plan)
    if not app.config.get("TESTING"):
        threading.Thread(target=engine.run_test, args=(tid, pid, plan), daemon=True).start()
    flash(f"Exécution automatique démarrée ({count} fonctionnalité(s)) via IA", "success")
    return redirect(url_for("test_view", tid=tid))


@app.route("/tests/<int:tid>/step/<int:rid>", methods=["POST"])
@login_required
def scenario_step_mark(tid, rid):
    status = request.form.get("status", "").upper()
    if status not in ("PASS", "FAIL", "WARNING", "SKIPPED"):
        flash("Statut invalide", "error")
        return redirect(url_for("test_view", tid=tid))
    database.mark_manual_step(rid, status)
    return redirect(url_for("test_view", tid=tid))


@app.route("/tests/<int:tid>/finish", methods=["POST"])
@login_required
def scenario_finish(tid):
    database.finalize_manual_run(tid)
    flash("Parcours terminé. Rapport généré.", "success")
    return redirect(url_for("report_detail", tid=tid))


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
    "max_buttons": 1000,
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
    run_type = getattr(test, "run_type", "")
    if run_type == "Exploration":
        template = "exploration.html"
    elif run_type == "Scénario":
        template = "scenario_run.html"
    else:
        template = "test_run.html"
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


def _parse_desc_to_steps(description):
    """Parse a description text into individual actionable steps.

    Handles various formats:
      - Numbered: '1. Cliquer sur Ajouter 2. Remplir le champ...'
      - Newlines: 'Cliquer sur Ajouter\nRemplir le champ...'
      - Periods: 'Cliquer sur Ajouter. Remplir le champ...'
      - Semicolons: 'Cliquer sur Ajouter; Remplir le champ...'
      - Commas: 'Cliquer sur Ajouter, remplir le champ...'
      - French connectors: 'Cliquer puis remplir puis valider'
    """
    import re
    if not description:
        return []
    text = description.strip()

    # 1) Try splitting by newlines
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) > 1:
        return [_clean_step(s) for s in lines if _clean_step(s)]

    # 2) Try splitting by numbered patterns (1. 2. 3.)
    numbered = re.split(r'(?<!\d)\d+[\.)]\s*', text)
    numbered = [s.strip() for s in numbered if s.strip()]
    if len(numbered) > 1:
        return [_clean_step(s) for s in numbered if _clean_step(s)]

    # 3) Try splitting by semicolons
    semicolons = [s.strip() for s in text.split(';') if s.strip()]
    if len(semicolons) > 1:
        return [_clean_step(s) for s in semicolons if _clean_step(s)]

    # 4) Try splitting by French connectors: puis, ensuite, après, et puis
    _CONNECTORS = r'(?<=\s)(?:puis|ensuite|après|et puis|et ensuite)\s+'
    parts_conn = re.split(_CONNECTORS, text, flags=re.IGNORECASE)
    parts_conn = [s.strip() for s in parts_conn if s.strip()]
    if len(parts_conn) > 1:
        return [_clean_step(s) for s in parts_conn if _clean_step(s)]

    # 5) Try splitting by periods (but not abbreviations)
    periods = [s.strip() for s in re.split(r'\.\s+(?=[A-ZÀ-Ö])', text) if s.strip()]
    if len(periods) > 1:
        return [_clean_step(s) for s in periods if _clean_step(s)]

    # 6) Try splitting by commas when followed by an action verb
    #    e.g. "Cliquer sur Ajouter, remplir le champ Nom, sauvegarder"
    _VERB_RE = (r'(?i)(?:cliquer|cliquez|remplir|remplissez|saisir|saisissez|'
                r'entrer|entrez|vérifier|vérifiez|cocher|cochez|sélectionner|'
                r'sélectionnez|choisir|choisissez|fermer|fermez|ouvrir|ouvrez|'
                r'attendre|patienter|appuyer|soumettre|valider|sauver|sauvez|'
                r'enregistrer|supprimer|supprimez|annuler|annulez|rechercher|'
                r'naviguer|aller|passez|clique|sauvegarder|sauvegardez)')
    comma_parts = re.split(r',\s*(?=' + _VERB_RE[4:] + r')', text)  # skip (?i) in split
    comma_parts = [s.strip() for s in comma_parts if s.strip()]
    if len(comma_parts) > 1:
        return [_clean_step(s) for s in comma_parts if _clean_step(s)]

    # 7) Single block — try to split on action verbs (case-insensitive)
    verbs = (r'(?i)(?=(?:cliquer|remplir|saisir|entrer|vérifier|vérif|cocher|'
             r'sélectionner|choisir|fermer|ouvrir|attendre|'
             r'appuyer|soumett|valider|sauver|enregistrer|supprimer|'
             r'annuler|rechercher|naviguer|aller|cliquez|remplissez|'
             r'vérifiez|saisissez|entrez|cochez|sélectionnez|allez|'
             r'faites|passez|clique))')
    parts = re.split(verbs, text)
    parts = [s.strip() for s in parts if s.strip()]
    if len(parts) > 1:
        return [_clean_step(s) for s in parts if _clean_step(s)]

    # 8) Last resort: return the whole description as one step
    return [text]


def _clean_step(s):
    """Remove leading/trailing punctuation and whitespace from a step."""
    s = s.strip().strip('.,;:').strip()
    return s


def _build_ai_plan(pid, fids):
    """Build an AI-driven plan (one ai_step per selected functionality).

    Each ai_step carries the functionality description plus its ordered steps
    as a single natural-language instruction, so the runner can drive the
    browser through them. Returns (plan, count) or (None, 0) if nothing
    selected.
    """
    fids = [int(f) for f in fids if str(f).isdigit()]
    if not fids:
        return None, 0

    plan = []
    for fid in fids:
        fn = database.get_functionality(fid)
        if not fn:
            continue
        module = database.get_module(fn["module_id"])
        module_name = module["name"] if module else "Général"
        module_desc = module.get("description") if module else ""
        path = (fn.get("path") or "").strip()
        folder_path = _parse_path(path)
        steps = database.list_steps(fid)
        step_texts = [s["description"] for s in steps if s["description"]]
        fn_desc = (fn.get("description") or "").strip()

        # Si pas d'étapes explicites, parser la description en étapes
        # individuelles pour que l'IA les exécute une par une.
        if not step_texts and fn_desc:
            step_texts = _parse_desc_to_steps(fn_desc)

        if step_texts:
            desc = ". ".join(step_texts)
        elif fn_desc:
            desc = fn_desc
        else:
            desc = (
                f"Tester la fonctionnalité '{fn['name']}' du module '{module_name}'. "
                f"Naviguer vers cette fonctionnalité puis vérifier qu'elle est "
                f"accessible et fonctionnelle."
            )
        plan.append({
            "step_type": "ai_step",
            "module": module_name,
            "module_description": module_desc or "",
            "function": fn["name"],
            "function_description": fn_desc,
            "label": fn["name"],
            "nav_path": folder_path,
            "url": (fn.get("url") or "").strip(),
            "user_desc": desc,
            "steps": step_texts,
        })
    return plan, len(plan)


def _parse_path(path):
    """Parse a navigation path like 'Facturation > Contrats > Service' into
    ordered segments: ['Facturation', 'Contrats', 'Service']. Accepts '>' and
    '/' as separators and strips whitespace."""
    if not path:
        return []
    separators = re.split(r"[>\u203a/]|::", path)
    return [seg.strip() for seg in separators if seg.strip()]


def _build_manual_run(pid, fids):
    """Create a manual scenario test run from selected functionality ids.

    Returns (tid, total_steps) or None if nothing selected / invalid. Every
    step of every selected functionality is pre-created as a (pending)
    test_result row, ready to be validated manually.
    """
    fids = [int(f) for f in fids if str(f).isdigit()]
    if not fids:
        return None

    tid = database.create_test_run(pid, "Scénario", session.get("user_email", ""))
    total = 0
    for fid in fids:
        fn = database.get_functionality(fid)
        if not fn:
            continue
        module = database.get_module(fn["module_id"])
        module_name = module["name"] if module else "Général"
        fn_desc = fn.get("description") or ""
        steps = database.list_steps(fid)
        if not steps:
            # a functionality without explicit steps is still a valid check item
            database.save_result(tid, {
                "module": module_name, "function": fn["name"],
                "action": "Fonctionnalité sélectionnée", "data": "",
                "severity": "", "status": "PENDING",
                "expected": fn_desc, "obtained": "",
            }, "")
            total += 1
            continue
        for st in steps:
            database.save_result(tid, {
                "module": module_name,
                "function": fn["name"],
                "action": f"Étape {st['step_order']} : {st['description']}",
                "data": "",
                "severity": "", "status": "PENDING",
                "expected": fn_desc, "obtained": "",
            }, "")
            total += 1
    if total == 0:
        # nothing valid was selected -> clean up the empty run
        database.delete_test_run(tid)
        return None
    database.finalize_manual_run(tid, running=True)
    return tid, total
