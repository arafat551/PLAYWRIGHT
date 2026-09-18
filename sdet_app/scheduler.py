"""Scheduler automatique : vérifie périodiquement les scheduled_runs et
lance les scénarios qui sont dus.

Tourne en arrière-plan (daemon thread) démarré au lancement du serveur.
"""
import threading
import time
from datetime import datetime

from . import database
from .sdet import engine, planner


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[SCHEDULER {ts}] {msg}", flush=True)


def _build_plan_for_project(pid):
    """Construire le plan de test pour un projet (toutes les fonctionnalités actives)."""
    try:
        fids = database.list_all_functionalities(pid)
        active_fids = [f["id"] for f in fids if f.get("is_active", 1)]
        if not active_fids:
            return None, 0

        # Importer _build_ai_plan depuis app (évite les imports circulaires)
        from .app import _build_ai_plan
        plan, count = _build_ai_plan(pid, active_fids)
        return plan, count
    except Exception as e:
        _log(f"Erreur construction plan pour projet {pid}: {e}")
        return None, 0


def _execute_scheduled_run(sched):
    """Exécuter un scheduled run."""
    pid = sched["project_id"]
    sid = sched["id"]
    project_name = sched.get("project_name", f"Projet#{pid}")

    _log(f"Démarrage: {project_name} (scheduled_run #{sid})")

    plan, count = _build_plan_for_project(pid)
    if not plan:
        _log(f"Pas de plan pour {project_name} — skip")
        database.mark_scheduled_run_done(sid)
        return

    try:
        tid = database.create_test_run(
            pid, "Scénario (auto)", "scheduler", plan=plan)
        engine.run_test(tid, pid, plan, headless=True)
        _log(f"Terminé: {project_name} — run #{tid} ({count} fonctionnalité(s))")
    except Exception as e:
        _log(f"Erreur exécution {project_name}: {e}")

    database.mark_scheduled_run_done(sid)


def _scheduler_loop():
    """Boucle principale du scheduler — tourne en arrière-plan."""
    _log("Scheduler démarré")
    while True:
        try:
            due = database.get_due_scheduled_runs()
            for sched in due:
                try:
                    _execute_scheduled_run(sched)
                except Exception as e:
                    _log(f"Erreur scheduled_run #{sched.get('id')}: {e}")
        except Exception as e:
            _log(f"Erreur boucle scheduler: {e}")
        # Vérifier toutes les 60 secondes
        time.sleep(60)


_scheduler_thread = None


def start_scheduler():
    """Démarrer le scheduler en arrière-plan (appelé au lancement du serveur)."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return  # déjà en cours
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()
    _log("Thread scheduler lancé")
