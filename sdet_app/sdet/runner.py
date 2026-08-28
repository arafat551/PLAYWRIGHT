"""Runner: executes a scenario plan with Playwright (section 21-22-29).

Each scenario is executed as a full workflow, not button-by-button.
The runner finds the row containing the SDET marker it created and acts on
that exact row. It only ever deletes its own created data.
"""
import os
import re
from urllib.parse import urlparse

from ..config import env
from . import forms, ai
from .classification import ICON_LABELS
from .verifier import snapshot, verdict_with_ai, access_verdict
from .browser import CRITICAL_HTTP

# ICON_LABELS maps emoji -> human label (e.g. "👁" -> "Visualiser")
_ICON_REV = dict(ICON_LABELS)

CREATE_RETRY = 3


def _record(db, tid, res, screenshot="", http_status=0):
    db.save_result(tid, res, screenshot, http_status)


def _shot_filename(project, module, action, status, seq):
    proj = re.sub(r"[^A-Za-z0-9_-]", "_", str(project)).upper()
    mod = re.sub(r"[^A-Za-z0-9_-]", "_", str(module))
    act = re.sub(r"[^A-Za-z0-9_-]", "_", str(action))
    return f"{proj}_{mod}_{act}_{status}_{seq:03d}.png"


def _save_screenshot(page, fname):
    # Saved under static/screenshots so `url_for('static', filename=...)`
    # serves them directly in the web UI.
    try:
        shot_dir = os.path.join(env.STATIC_DIR, "screenshots")
        os.makedirs(shot_dir, exist_ok=True)
        path = os.path.join(shot_dir, fname)
        page.screenshot(path=path, full_page=False)
        return f"screenshots/{fname}"
    except Exception:
        return ""


def _is_same_url(a, b):
    return urlparse(a).path.rstrip("/") == urlparse(b).path.rstrip("/")


class Runner:
    def __init__(self, session, db, project, tid):
        self.session = session
        self.page = session.page
        self.db = db
        self.project = project
        self.tid = tid
        self.suffix = None
        self.entity_name = None
        self.shot_seq = 0

    # -- helpers ----------------------------------------------------------

    def goto(self, url):
        try:
            resp = self.session.goto(url, timeout=20000)
            return resp.status if resp else 0
        except Exception:
            return 0

    def _module_label(self, mod):
        return mod

    def _screenshot(self, module, action, status):
        self.shot_seq += 1
        return _save_screenshot(self.page, _shot_filename(
            self.project.get("name", "PROJET"), module, action, status, self.shot_seq))

    def _record(self, res, status, expected, obtained, screenshot="", http=0):
        res["status"] = status
        res["expected"] = expected
        res["obtained"] = obtained
        _record(self.db, self.tid, res, screenshot, http)
        return res

    def _verify(self, module, func, action, data, before, after,
                expected, project_context=""):
        """Apply verifier, taking screenshot on FAIL/WARNING, record result."""
        help_evaluate = ai.help_evaluate if env.OPENAI_API_KEY else None
        v = verdict_with_ai(before, after, expected, project_context, action,
                            help_evaluate, data)
        status = v["status"]
        shot = self._screenshot(module, action, status) if status in ("FAIL", "WARNING") else ""
        self._record({
            "module": module, "function": func, "action": action, "data": data,
            "severity": v.get("severity", ""),
        }, status, v.get("expected", expected), v.get("obtained", ""), shot)
        return status

    # -- row targeting -----------------------------------------------------

    def _find_row(self, text):
        """Return the <tr> containing `text`, or None."""
        try:
            trs = self.page.query_selector_all("table tbody tr, table tr")
            for tr in trs:
                try:
                    if text in (tr.inner_text() or ""):
                        return tr
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _click_in_row(self, row, target_label):
        """Click an action (by label) inside a specific table row."""
        # Try the exact label first, then extenders (Visualiser, etc)
        try:
            btn = row.query_selector(f'text="{target_label}"')
            if btn:
                btn.click()
                return True
        except Exception:
            pass
        try:
            links = row.query_selector_all("button, [role='button'], a")
        except Exception:
            links = []
        target = target_label.lower()
        for el in links:
            try:
                text = (el.inner_text() or "").strip()
                title = (el.get_attribute("title") or "").lower()
                aria = (el.get_attribute("aria-label") or "").lower()
                if target in text.lower() and len(text) < 40:
                    el.click()
                    return True
                if title and target in title:
                    el.click()
                    return True
                if aria and target in aria:
                    el.click()
                    return True
                # icon-only match (e.g. 👁 -> Visualiser)
                if _ICON_REV.get(text) and target in _ICON_REV[text].lower():
                    el.click()
                    return True
            except Exception:
                continue
        return False

    def _type_to_label(self, a_type):
        mapping = {
            "CREATE": "Ajouter",
            "READ": "Visualiser",
            "UPDATE": "Modifier",
            "DELETE": "Supprimer",
            "STATUS": "Désactiver",
        }
        return mapping.get(a_type.upper())

    # -- workflow steps ------------------------------------------------------

    def access(self, step):
        mod = step["module"]
        url = step["url"]
        http = self.goto(url)
        v = access_verdict(http)
        shot = self._screenshot(mod, "Access", v["status"]) if v["status"] == "FAIL" else ""
        self._record({
            "module": mod, "function": "Accès page", "action": "NAVIGATION",
            "data": url, "severity": v["severity"],
        }, v["status"], "La page répond au chargement",
        f"HTTP {http or 'inconnu'} - {self.page.url}", shot, http)
        return v["status"]

    def create(self, step):
        mod = step["module"]
        url = step["url"]
        src = step["source_action"]
        label = step["label"]

        # open the create form (navigate back first if needed)
        if not _is_same_url(self.page.url, url):
            self.goto(url)

        # click the create button
        before = snapshot(self.page)
        clicked = self._click_label(src, label)
        if not clicked:
            self._record({"module": mod, "function": label, "action": "CREATE",
                          "data": "", "severity": "MAJOR"},
                         "FAIL", "Le bouton de creation est present",
                         "Bouton de creation introuvable",
                         self._screenshot(mod, "Create", "FAIL"))
            return "FAIL"

        self.suffix = forms.unique_suffix()
        self.entity_name = f"QA_{mod.upper()}_{self.suffix}" if len(mod.split()) <= 1 \
            else f"QA_{ ''.join(re.findall(r'[A-Za-z0-9]', mod))[:10].upper()}_{self.suffix}"

        # find form & fill
        form_el = forms.find_form(self.page)
        if form_el:
            filled, _ = forms.fill_form(self.page, form_el, self.suffix, self.entity_name)
        else:
            filled = 0
        if filled == 0:
            self._record({"module": mod, "function": label, "action": "CREATE",
                          "data": self.entity_name, "severity": "WARNING"},
                         "WARNING", "Un formulaire est attendu",
                         "Aucun champ remplissable détecté",
                         self._screenshot(mod, "Create", "WARNING"))
            return "WARNING"

        before_submit = snapshot(self.page)
        forms.submit_form(self.page, form_el)
        after_submit = snapshot(self.page)
        status = self._verify(mod, label, "CREATE", self.entity_name,
                              before_submit, after_submit,
                              "La donnée " + str(self.entity_name) + " est créée")
        return status

    def search_create(self, step):
        """Search for the row created by this run to confirm it exists & is findable."""
        mod = step["module"]
        src = step["source_action"]
        if not self.entity_name:
            return "SKIPPED"
        before = snapshot(self.page)
        # locate the search input from the page
        s = self._find_search_input()
        if not s:
            self._record({"module": mod, "function": "Recherche", "action": "SEARCH",
                          "data": self.entity_name, "severity": "WARNING"},
                         "WARNING", "Un champ de recherche est présent",
                         "Champ de recherche introuvable",
                         self._screenshot(mod, "Search", "WARNING"))
            return "WARNING"
        try:
            s.fill(self.entity_name)
            s.press("Enter")
        except Exception:
            pass
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        self.page.wait_for_timeout(1200)
        after = snapshot(self.page)
        if self.entity_name.lower() in after["text"]:
            self._record({"module": mod, "function": "Recherche", "action": "SEARCH",
                          "data": self.entity_name, "severity": ""},
                         "PASS", "La donnée créée est retrouvée par recherche",
                         "La ligne " + str(self.entity_name) + " apparaît après la recherche")
            return "PASS"
        self._record({"module": mod, "function": "Recherche", "action": "SEARCH",
                      "data": self.entity_name, "severity": "MAJOR"},
                     "FAIL", "La donnée créée est retrouvée par recherche",
                     "La donnée créée n'apparaît pas dans les résultats",
                     self._screenshot(mod, "Search", "FAIL"))
        return "FAIL"

    def read(self, step):
        mod = step["module"]
        src = step["source_action"]
        label = step["label"]
        self.goto(step["url"])
        # find the created row (or fall back to general behaviour)
        row = self._find_row(str(self.entity_name)) if self.entity_name else None
        if not row:
            # no marker -> generic click (non destructive, still verified)
            return self._generic(step)
        before = snapshot(self.page)
        clicked = self._click_in_row(row, label) or self._click_in_row(row, self._type_to_label("READ"))
        if not clicked:
            self._record({"module": mod, "function": label, "action": "READ",
                          "data": self.entity_name, "severity": "WARNING"},
                         "WARNING", "Un bouton de consultation est présent",
                         "Bouton de consultation introuvable sur la ligne",
                         self._screenshot(mod, "Read", "WARNING"))
            return "WARNING"
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        self.page.wait_for_timeout(1000)
        after = snapshot(self.page)
        return self._verify(mod, label, "READ", self.entity_name, before, after,
                            "La consultation affiche les informations de la donnée")

    def update(self, step):
        mod = step["module"]
        src = step["source_action"]
        label = step["label"]
        self.goto(step["url"])
        if not self.entity_name:
            return self._generic(step)
        row = self._find_row(str(self.entity_name))
        if not row:
            return self._generic(step)
        clicked = self._click_in_row(row, label) or self._click_in_row(row, self._type_to_label("UPDATE"))
        if not clicked:
            self._record({"module": mod, "function": label, "action": "UPDATE",
                          "data": self.entity_name, "severity": "MAJOR"},
                         "FAIL", "Un bouton de modification est présent",
                         "Bouton de modification introuvable sur la ligne",
                         self._screenshot(mod, "Edit", "FAIL"))
            return "FAIL"
        form_el = forms.find_form(self.page)
        if not form_el:
            self._record({"module": mod, "function": label, "action": "UPDATE",
                          "data": self.entity_name, "severity": "WARNING"},
                         "WARNING", "Un formulaire de modification est attendu",
                         "Formulaire de modification introuvable",
                         self._screenshot(mod, "Edit", "WARNING"))
            return "WARNING"
        before = snapshot(self.page)
        # Change one field so the modification is provable
        changed = forms.toggle_field(self.page, form_el, self.suffix, self.entity_name)
        forms.submit_form(self.page, form_el)
        after = snapshot(self.page)
        return self._verify(mod, label, "UPDATE", self.entity_name, before, after,
                            "La modification est conservée")

    def generic(self, step):
        # STATUS-type actions (Désactiver / Activer) must target the created row
        if step.get("action") == "STATUS" and self.entity_name:
            return self._status_on_row(step)
        if step.get("action") in ("READ", "UPDATE", "STATUS", "DELETE") and self.entity_name:
            row = self._find_row(str(self.entity_name))
            if row:
                return self._row_action(step, row)
        return self._generic(step)

    def _status_on_row(self, step):
        mod = step["module"]
        label = step["label"]
        self.goto(step["url"])
        row = self._find_row(str(self.entity_name))
        if not row:
            self._record({"module": mod, "function": label, "action": "STATUS",
                          "data": self.entity_name, "severity": "WARNING"},
                         "WARNING", "La ligne à modifier est présente",
                         "Ligne non trouvée sur la page courante",
                         self._screenshot(mod, "Status", "WARNING"))
            return "WARNING"
        before = snapshot(self.page)
        clicked = (self._click_in_row(row, label)
                   or self._click_in_row(row, "Désactiver")
                   or self._click_in_row(row, "Activer"))
        if not clicked:
            self._record({"module": mod, "function": label, "action": "STATUS",
                          "data": self.entity_name, "severity": "WARNING"},
                         "WARNING", "Un bouton de changement de statut est présent",
                         "Bouton statut introuvable sur la ligne",
                         self._screenshot(mod, "Status", "WARNING"))
            return "WARNING"
        self._after_click()
        after = snapshot(self.page)
        return self._verify(mod, label, "STATUS", self.entity_name, before, after,
                            "Le statut de la donnée a changé (message de confirmation)")

    def _row_action(self, step, row):
        """Generic read/update/delete on the created row, delegating to the
        dedicated flow if the matching step type exists."""
        s = step["step_type"]
        if s == "read":
            return self.read(step)
        if s == "update":
            return self.update(step)
        if s == "delete_last":
            return self.delete_last(step)
        return self._generic(step)

    def _generic(self, step):
        mod = step["module"]
        src = step["source_action"]
        label = step["label"]
        self.goto(step["url"])
        before = snapshot(self.page)
        clicked = self._click_label(src, label)
        if not clicked:
            self._record({"module": mod, "function": label, "action": step.get("action", "GENERIC"),
                          "data": self.entity_name, "severity": "WARNING"},
                         "WARNING", "L'élément est présent et cliquable",
                         "Élément introuvable",
                         self._screenshot(mod, "Generic", "WARNING"))
            return "WARNING"
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        self.page.wait_for_timeout(1200)
        after = snapshot(self.page)
        return self._verify(mod, label, step.get("action", "GENERIC"), self.entity_name,
                            before, after, "L'élément produit une réaction visible")

    def delete_last(self, step):
        mod = step["module"]
        src = step["source_action"]
        label = step["label"]
        # SAFETY: only delete if we created this data (section 29)
        if not self.entity_name:
            self._record({"module": mod, "function": label, "action": "DELETE",
                          "data": "", "severity": "MINOR"},
                         "SKIPPED", "La suppression de données préexistantes est interdite",
                         "Aucune donnée créée par ce run -> suppression refusée")
            return "SKIPPED"
        self.goto(step["url"])
        row = self._find_row(str(self.entity_name))
        if not row:
            self._record({"module": mod, "function": label, "action": "DELETE",
                          "data": self.entity_name, "severity": "WARNING"},
                         "SKIPPED", "La ligne à supprimer est présente",
                         "La ligne " + str(self.entity_name) + " n'a pas été retrouvée à la suppression")
            return "SKIPPED"
        before = snapshot(self.page)
        clicked = self._click_in_row(row, label) or self._click_in_row(row, self._type_to_label("DELETE"))
        if not clicked:
            self._record({"module": mod, "function": label, "action": "DELETE",
                          "data": self.entity_name, "severity": "MAJOR"},
                         "FAIL", "Un bouton de suppression est présent",
                         "Bouton de suppression introuvable sur la ligne",
                         self._screenshot(mod, "Delete", "FAIL"))
            return "FAIL"
        # confirmation dialog
        try:
            self.page.on("dialog", lambda d: d.accept())
            if self.page.locator("[role='dialog']").count() >= 0:
                pass
        except Exception:
            pass
        self.page.wait_for_timeout(1000)
        # click any visible confirm button if a dialog appeared
        self._try_confirm()
        after = snapshot(self.page)
        status = self._verify(mod, label, "DELETE", self.entity_name, before, after,
                              "La donnée créée est supprimée")
        return status

    def _try_confirm(self):
        for text in ["Supprimer", "Confirmer", "Oui", "Valider", "Delete", "OK"]:
            try:
                btn = self.page.query_selector(
                    f'[role="dialog"] button:has-text("{text}"), .modal button:has-text("{text}"), button:has-text("{text}")')
                if btn and btn.is_visible():
                    btn.click()
                    self.page.wait_for_timeout(800)
                    break
            except Exception:
                continue

    # -- label-based clicking ---------------------------------------------

    def _click_label(self, src, label):
        """Click a button by its discovered label/index."""
        label = (label or "").strip()
        try:
            idx = int(src.get("el_index") or -1)
            if idx >= 0:
                els = self.page.query_selector_all(
                    'button, [role="button"], input[type="submit"], input[type="button"], a.btn, a[class*="btn"], a[class*="button"]')
                if idx < len(els):
                    el = els[idx]
                    # Only trust the stored index when it still points to an
                    # element matching the label (the page may have been rebuilt
                    # between the scan and the run).
                    if self._el_matches(el, label):
                        try:
                            el.click(timeout=4000)
                            self._after_click()
                            return True
                        except Exception:
                            pass
        except Exception:
            pass
        # fallback: by role/name or text
        return self._click_by_text(label[:40])

    def _el_matches(self, el, label):
        lab = (label or "").lower()
        try:
            text = (el.inner_text() or "").strip()
            if text and len(text) <= 40:
                if lab in text.lower():
                    return True
                ic = _ICON_REV.get(text)
                if ic and ic.lower() in lab:
                    return True
            for attr in ("aria-label", "title", "value"):
                v = el.get_attribute(attr) or ""
                if v and lab in v.lower():
                    return True
        except Exception:
            pass
        return False

    def _click_by_text(self, text):
        rx = re.compile(re.escape(text), re.IGNORECASE)
        for role in ("button", "link", "menuitem"):
            try:
                self.page.get_by_role(role, name=rx).first.click(timeout=4000)
                self._after_click()
                return True
            except Exception:
                continue
        try:
            self.page.get_by_text(rx).first.click(timeout=4000)
            self._after_click()
            return True
        except Exception:
            pass
        # icon-only fallback: scan for title / aria / icon glyph
        try:
            els = self.page.query_selector_all(
                'button, [role="button"], a, input[type="submit"], input[type="button"]')
            for el in els:
                if self._el_matches(el, text):
                    try:
                        el.click(timeout=4000)
                        self._after_click()
                        return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False

    def _after_click(self):
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        self.page.wait_for_timeout(800)

    def _find_search_input(self):
        try:
            return self.page.query_selector(
                'input[type="search"], input[name*="search" i], input[name*="query" i], input[placeholder*="recherch" i], input[placeholder*="search" i]')
        except Exception:
            return None


def run_scenarios(session, db, project, tid, steps, launch_info=None,
                  is_cancelled=None):
    """Run a list of planner steps. Returns (counters, results)."""
    runner = Runner(session, db, project, tid)
    counters = {"total": 0, "passed": 0, "failed": 0, "warning": 0, "skipped": 0}

    def add(status):
        counters["total"] += 1
        key = {"PASS": "passed", "FAIL": "failed",
               "WARNING": "warning", "SKIPPED": "skipped"}.get(status, "skipped")
        counters[key] += 1

    results = []
    for step in steps:
        if is_cancelled and is_cancelled():
            break
        st = step["step_type"]
        try:
            if st == "access":
                status = runner.access(step)
            elif st == "create":
                status = runner.create(step)
            elif st == "search_create":
                status = runner.search_create(step)
            elif st == "read":
                status = runner.read(step)
            elif st == "update":
                status = runner.update(step)
            elif st == "delete_last":
                status = runner.delete_last(step)
            else:
                status = runner.generic(step)
        except Exception as e:
            status = "FAIL"
            runner._record({
                "module": step.get("module", "Général"),
                "function": step.get("label", "Scénario"),
                "action": step.get("action", "GENERIC"),
                "data": getattr(runner, "entity_name", ""),
                "severity": "CRITICAL",
            }, "FAIL", "Le scénario s'exécute sans erreur",
            f"Exception: {e}",
            runner._screenshot(step.get("module", "Général"), "Exec", "FAIL"))
        add(status)

    # Add HTTP errors (non-destructive reporting) at end
    _record_http_errors(runner, db, tid, counters, results)
    return counters, results


def _record_http_errors(runner, db, tid, counters, results):
    errs = runner.session.drain_http_errors()
    for e in errs:
        status = "WARNING" if e["status"] < 500 else "FAIL"
        runner._record({
            "module": "HTTP", "function": "Requête HTTP",
            "action": e["url"], "data": "",
            "severity": "MAJOR" if status == "FAIL" else "MINOR",
        }, status,
        "Aucune erreur HTTP importante",
        f"Code {e['status']} - {e['url']}", http=e["status"])
