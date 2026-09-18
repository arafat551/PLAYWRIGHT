"""Engine orchestration: coordinates explorer -> planner -> runner and
persists results. Runs inside background threads started by app.py.
"""
import os
import time
from datetime import datetime

from .. import database
from . import browser, explorer, planner, reporter
from .browser import open_session


def _log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[ENGINE {ts}] {msg}", flush=True)


def _log_net_err(url, e):
    _log(f"Erreur réseau sur {url}: {e}")


def _project_dict(pid):
    p = database.get_project(pid)
    if not p:
        return None
    return dict(p.row)


def _is_cancelled(tid):
    return database.test_status(tid) in ("cancel_requested",)


def _authenticate(session, project, tid):
    """Authentifier la session en une seule étape (login simple ou 2FA) puis
    récupérer le code OTP automatiquement quand la page l'exige.

    1. browser.authenticate : login + code OTP lu dans la boîte mail (IMAP).
    2. Si le code n'a pas pu être récupéré automatiquement, on passe le run en
       'waiting_otp' pour que l'opérateur le saisisse dans l'interface (l'ancien
       comportement), sans fermer le navigateur.

    Retourne True pour continuer le run, False si le run a été annulé.
    """
    from .browser import authenticate, fill_otp_code

    if authenticate(session.page, project):
        return True

    database.set_waiting_otp(tid)
    while True:
        time.sleep(2)
        status = database.test_status(tid)
        if status == "otp_submitted":
            try:
                row = database.get_test_run(tid)
                code = getattr(row, "otp_code", "") or ""
                if code:
                    fill_otp_code(session.page, code)
                    browser.settle(session.page)
            except Exception as e:
                _log(f"OTP manuel: {e}")
            return True
        if status == "cancel_requested":
            session.close()
            database.finalize_test_run(tid, "cancelled", _zero(), [])
            return False


def run_exploration(tid, pid):
    """Discover pages + actions of a project and persist the cartography."""
    project = _project_dict(pid)
    if not project:
        database.finalize_test_run(tid, "failed", {"total": 0, "passed": 0, "failed": 0, "warning": 0, "skipped": 0}, [])
        return

    session = None
    stop = None
    try:
        session, stop = open_session()
        page = session.page
        try:
            session.goto(project["url"])
            browser.settle(page)
        except Exception as e:
            msg = str(e).lower()
            if "net::err" in msg or "network" in msg:
                _log_net_err(project["url"], e)
                try:
                    session.goto(project["url"], wait_for="commit",
                                 timeout=60000)
                    browser.settle(page)
                except Exception:
                    raise RuntimeError(f"Exploration - impossible de charger l'URL: {e}")
            else:
                raise RuntimeError(f"Exploration - impossible de charger l'URL: {e}")

        # Authentification unique (login simple/2FA + OTP automatique),
        # sinon saisie manuelle du code dans l'interface.
        if not _authenticate(session, project, tid):
            return

        def progress(n):
            database.set_running_total(tid, n)

        found, actions = explorer.explore(
            session, project,
            on_progress=progress,
            is_cancelled=lambda: _is_cancelled(tid))

        if _is_cancelled(tid):
            # Arrêt demandé : ne pas écraser la cartographie existante.
            database.finalize_test_run(tid, "cancelled", _zero(), [])
            return

        if found:
            pages = [{"name": info["name"], "url": u, "status": info["status"]}
                     for u, info in found.items()]
            database.replace_pages_and_actions(pid, pages, actions)
        # found vide (échec de login / site indisponible) : on garde la
        # cartographie d'une exploration précédente au lieu de tout effacer.
        database.mark_explored(tid, len(found))
    except Exception as e:
        database.save_test_error(tid, str(e))
    finally:
        if session:
            try:
                session.close()
            except Exception:
                pass
        if stop:
            try:
                stop()
            except Exception:
                pass


def run_test(tid, pid, plan):
    """Execute a previously-built plan (scenario steps) on the project."""
    project = _project_dict(pid)
    if not project:
        database.finalize_test_run(tid, "failed", _zero(), [])
        return

    project["environment"] = project.get("environment", "STAGING")

    session = None
    stop = None
    start = time.time()
    final_status = None
    try:
        session, stop = open_session()
        page = session.page
        try:
            session.goto(project["url"])
            browser.settle(page)
        except Exception as e:
            msg = str(e).lower()
            if "net::err" in msg or "network" in msg:
                _log_net_err(project["url"], e)
                try:
                    session.goto(project["url"], wait_for="commit",
                                 timeout=60000)
                    browser.settle(page)
                except Exception:
                    raise RuntimeError(f"Test - impossible de charger l'URL: {e}")
            else:
                raise RuntimeError(f"Test - impossible de charger l'URL: {e}")

        # Authentification unique (login simple/2FA + OTP automatique),
        # sinon saisie manuelle du code dans l'interface.
        if not _authenticate(session, project, tid):
            return

        counters, results = _run_impl(session, project, tid, plan,
                                      is_cancelled=lambda: _is_cancelled(tid))
        duration = time.time() - start
        final_status = "cancelled" if _is_cancelled(tid) else "completed"
        database.finalize_test_run(tid, final_status, counters, results)
        reporter.write_report_file(project, {"started_at": datetime.now().isoformat(),
                                             "run_type": database.get_test_run(tid).run_type,
                                             "launched_by": database.get_test_run(tid).launched_by,
                                             "id": tid}, results, counters, duration)
    except Exception as e:
        database.save_test_error(tid, str(e))
    finally:
        if session:
            try:
                session.close()
            except Exception:
                pass
        if stop:
            try:
                stop()
            except Exception:
                pass
    if final_status == "completed":
        try:
            from .email_report import send_run_report
            send_run_report(tid)
        except Exception as e:
            _log(f"Envoi du rapport par e-mail impossible: {e}")


def _run_impl(session, project, tid, plan, is_cancelled=None):
    from .runner import run_scenarios
    return run_scenarios(session, database, project, tid, plan,
                         is_cancelled=is_cancelled)


def _zero():
    return {"total": 0, "passed": 0, "failed": 0, "warning": 0, "skipped": 0}
