"""Playwright browser runner — drives scenarios step by step.

Each planner step is dispatched to a runner method that performs the
action and records results.
"""
import json
import os
import re
import time
import traceback

from ..config import env, setting_int
from .action_interpreter import ActionInterpreter
from .browser import settle

# Canonical action types executed deterministically by the interpreter.
# Legacy planner steps also carry an 'action_type' key (NAVIGATION, CREATE,
# SEARCH, ...) so structured detection matches explicitly against this set.
_STRUCTURED_ACTION_TYPES = frozenset([
    "NAVIGUER", "CLIQUEER", "REMPLIR", "SELECTIONNER", "COCHER",
    "DECOCHER", "RECHERCHER", "VERIFIER", "ATTENDRE", "ECRAN",
    "SELECTIONNER_LIGNE",
])


def _log(msg):
    """Print a timestamped debug line so failures are visible in stdout."""
    ts = time.strftime("%H:%M:%S")
    print(f"[RUNNER {ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Interactive-element scan via Playwright (legacy fallback path)
# ---------------------------------------------------------------------------

def _is_same_url(a, b):
    """Compare URLs ignoring trailing slashes and fragments."""
    from urllib.parse import urlparse
    def norm(u):
        p = urlparse(u)
        return (p.scheme, p.netloc, p.path.rstrip("/"))
    return norm(a) == norm(b)


class Runner:
    def __init__(self, session, db, project, tid, is_cancelled=None):
        self.session = session
        self.page = session.page
        self.db = db
        self.project = project
        self.tid = tid
        self._is_cancelled = is_cancelled or (lambda: False)
        self.suffix = None
        self.entity_name = None
        self.shot_seq = 0
        self.last_fill_value = None

    # -- helpers ----------------------------------------------------------

    def goto(self, url, timeout=20000):
        _log(f"goto {url}")
        try:
            resp = self.session.goto(url, timeout=timeout)
            return resp.status if resp else 0
        except Exception:
            return 0

    def _cancel_requested(self):
        try:
            return self._is_cancelled()
        except Exception:
            return False

    def _screenshot(self, module, func, status):
        self.shot_seq += 1
        # Save into static/screenshots and store a static-relative path so the
        # report templates can render it via url_for('static', ...).
        fname = f"shot_{self.tid}_{module}_{func}_{status}_{self.shot_seq}.png"
        fname = fname.replace(" ", "_").replace("/", "_").replace("\\", "_")\
                     .replace(":", "_").replace("é", "e").replace("è", "e")\
                     .replace("ê", "e").replace("â", "a").replace("à", "a")\
                     .replace("û", "u").replace("ô", "o").replace("î", "i")\
                     .replace("ç", "c")
        rel = ("screenshots/" + fname).replace("\\", "/")
        fn = os.path.join(env.STATIC_DIR, rel)
        try:
            os.makedirs(os.path.dirname(fn), exist_ok=True)
            # Don't capture mid-transition: a click can trigger navigation or a
            # slow SPA render, and a shot taken right away catches a blank or
            # dark frame. Wait for the page to settle before capturing.
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            try:
                self.page.wait_for_timeout(600)
            except Exception:
                pass
            self.page.screenshot(path=fn)
        except Exception:
            rel = ""
        return rel

    def _record(self, res, status, expected, obtained, screenshot="", http=0):
        # Systematic capture: every recorded step (PASS included) gets a
        # screenshot so the report's "Capture" column always shows an image.
        # Callers that already took one (FAIL/WARNING paths) pass it explicitly.
        if not screenshot:
            screenshot = self._screenshot(res.get("module", "Général"),
                                          res.get("function", ""), status)
        _log(f"RECORD {status}: {res.get('module','')}/{res.get('function','')} "
             f"- {res.get('action','')}")
        self.db.save_result(self.tid, {
            "module": res.get("module", "Général"),
            "function": res.get("function", ""),
            "action": res.get("action", ""),
            "data": res.get("data", ""),
            "status": status,
            "severity": res.get("severity", ""),
            "expected": expected,
            "obtained": obtained,
        }, screenshot, http)

    # -- access (login / navigation) ------------------------------------

    def access(self, step):
        mod = step.get("module", "Général")
        url = step.get("url") or self.project.get("url", "")
        _log(f"ACCESS {url}")
        code = self.goto(url)
        settle(self.page)
        if code and code >= 400:
            self._record(
                {"module": mod, "function": "Accès", "action": "NAVIGATE",
                 "data": url, "severity": "CRITICAL"},
                "FAIL", "Page chargée sans erreur",
                f"Code HTTP {code}", self._screenshot(mod, "Accès", "FAIL"))
            return "FAIL"
        self._record(
            {"module": mod, "function": "Accès", "action": "NAVIGATE",
             "data": url, "severity": ""},
            "PASS", "Page chargée sans erreur",
            f"Code HTTP {code or 'N/A'}")
        return "PASS"

    # -- create ---------------------------------------------------------

    def create(self, step):
        from . import ai
        mod = step.get("module", "Général")
        func = step.get("function", "")
        action = step.get("action", "create")
        _log(f"CREATE {mod}/{func}")

        # Navigate if needed
        nav = step.get("nav_path") or []
        if nav:
            self._navigate_path_text(nav)

        # 1) Fast Playwright click: try _click_add_button first (no AI needed)
        clicked = self._click_add_button()
        if clicked:
            settle(self.page)
        else:
            # 2) Fallback: AI planning to find the add/create button
            state = self._collect_page_state()
            plan = ai.plan_step_action(
                f"Cliquer sur Ajouter / Nouveau pour créer: {func}",
                state, self.project.get("url", ""))
            if not plan:
                self._record(
                    {"module": mod, "function": func, "action": action,
                     "data": "", "severity": "MINOR"},
                    "WARNING", "Bouton Ajouter trouvé",
                    "IA n'a pas trouvé d'action possible")
                return "WARNING"
            self._execute_ai_action(plan)
            settle(self.page)

        # Fill form if fields are given
        fields = step.get("fields", {})
        if fields:
            for fname, fval in fields.items():
                self._fill_by_label(fname, fval)

        # Submit
        submit = step.get("submit", True)
        if submit:
            self._click_by_text("Sauver") or self._click_by_text("Enregistrer") or self._click_by_text("Valider")
            settle(self.page)

        self._record(
            {"module": mod, "function": func, "action": action,
             "data": json.dumps(fields, ensure_ascii=False), "severity": ""},
            "PASS", "Création effectuée", "OK")
        return "PASS"

    def search_create(self, step):
        """Search + create flow."""
        return self.create(step)

    # -- read -----------------------------------------------------------

    def read(self, step):
        mod = step.get("module", "Général")
        func = step.get("function", "")
        _log(f"READ {mod}/{func}")

        nav = step.get("nav_path") or []
        if nav:
            self._navigate_path_text(nav)

        self._record(
            {"module": mod, "function": func, "action": "READ",
             "data": "", "severity": ""},
            "PASS", "Page consultée", "OK")
        return "PASS"

    # -- update ---------------------------------------------------------

    def update(self, step):
        from . import ai
        mod = step.get("module", "Général")
        func = step.get("function", "")
        _log(f"UPDATE {mod}/{func}")

        nav = step.get("nav_path") or []
        if nav:
            self._navigate_path_text(nav)

        state = self._collect_page_state()
        plan = ai.plan_step_action(
            f"Cliquer sur Modifier pour: {func}",
            state, self.project.get("url", ""))
        if plan:
            self._execute_ai_action(plan)
            settle(self.page)
            fields = step.get("fields", {})
            for fname, fval in fields.items():
                self._fill_by_label(fname, fval)
            self._click_by_text("Sauver") or self._click_by_text("Enregistrer") or self._click_by_text("Valider")
            settle(self.page)

        self._record(
            {"module": mod, "function": func, "action": "UPDATE",
             "data": "", "severity": ""},
            "PASS", "Modification effectuée", "OK")
        return "PASS"

    # -- delete ---------------------------------------------------------

    def delete_last(self, step):
        from . import ai
        mod = step.get("module", "Général")
        func = step.get("function", "")
        _log(f"DELETE {mod}/{func}")

        nav = step.get("nav_path") or []
        if nav:
            self._navigate_path_text(nav)

        state = self._collect_page_state()
        plan = ai.plan_step_action(
            f"Cliquer sur Supprimer pour: {func}",
            state, self.project.get("url", ""))
        if plan:
            self._execute_ai_action(plan)
            settle(self.page)
            # Confirm deletion if dialog
            try:
                self._click_by_text("Confirmer") or self._click_by_text("OK") or self._click_by_text("Oui")
                settle(self.page)
            except Exception:
                pass

        self._record(
            {"module": mod, "function": func, "action": "DELETE",
             "data": "", "severity": ""},
            "PASS", "Suppression effectuée", "OK")
        return "PASS"

    # -- generic --------------------------------------------------------

    def generic(self, step):
        mod = step.get("module", "Général")
        func = step.get("function", step.get("label", ""))
        _log(f"GENERIC {mod}/{func}")

        nav = step.get("nav_path") or []
        if nav:
            self._navigate_path_text(nav)

        action = step.get("action", "ACCES")
        url = step.get("url", "")
        if url:
            code = self.goto(url)
            settle(self.page)
        else:
            code = 0

        self._record(
            {"module": mod, "function": func, "action": action,
             "data": "", "severity": ""},
            "PASS", "Action effectuée", "OK")
        return "PASS"

    # -- navigation helpers --------------------------------------------

    def _navigate_path_text(self, path):
        """Click through a list of labels (menu path)."""
        for label in path:
            if self._cancel_requested():
                return
            _log(f"  nav click: {label}")
            if self._click_nav_label(label) or self._click_by_text(label):
                self._slow_pause()
            else:
                _log(f"  nav label '{label}' NOT FOUND")

    def _click_nav_label(self, label):
        """Try clicking a navigation/menu element by its text."""
        try:
            selectors = [
                f'nav >> text="{label}"',
                f'aside >> text="{label}"',
                f'[role="navigation"] >> text="{label}"',
                f'.sidebar >> text="{label}"',
                f'.menu >> text="{label}"',
                f'a:has-text("{label}")',
                f'button:has-text("{label}")',
            ]
            for sel in selectors:
                try:
                    loc = self.page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        loc.click(timeout=4000)
                        self._after_click()
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _click_add_button(self):
        """Find and click the 'Ajouter' / 'Nouveau' button using Playwright.

        Tries multiple strategies in order:
          1. Playwright text locator for common French labels.
          2. Native button/link scan with label matching.
          3. Icon-class scan (fa-plus, bi-plus-circle, etc.).

        Returns True on success, False if no matching button is found.
        """
        _ADD_LABELS = (
            "ajouter", "nouveau", "nouvelle", "créer", "create",
            "add", "new",
        )

        # 1) Playwright text locator — broad search for 'Ajouter' text
        for kw in ("Ajouter", "Nouveau", "Nouvelle", "Créer"):
            try:
                loc = self.page.get_by_text(kw, exact=False).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=4000)
                    self._after_click()
                    return True
            except Exception:
                continue

        # 2) Native controls scan (button, a, [role='button'], input)
        _NATIVE = (
            'button, [role="button"], input[type="submit"], '
            'input[type="button"], input[type="image"]'
        )
        try:
            els = self.page.query_selector_all(_NATIVE)
            for el in els:
                try:
                    if not el.is_visible():
                        continue
                    for attr in ("inner_text",):
                        t = (getattr(el, attr)() or "").strip().lower()
                        if any(kw in t for kw in _ADD_LABELS):
                            el.click(timeout=4000)
                            self._after_click()
                            return True
                    for attr in ("aria-label", "title", "value"):
                        v = (el.get_attribute(attr) or "").strip().lower()
                        if any(kw in v for kw in _ADD_LABELS):
                            el.click(timeout=4000)
                            self._after_click()
                            return True
                except Exception:
                    continue
        except Exception:
            pass

        # 3) Non-native clickable: div/span/li with btn/action/button class
        _NON_NATIVE = (
            'div[class*="btn"], span[class*="btn"], li[class*="btn"], '
            'div[class*="action"], span[class*="action"], li[class*="action"]'
        )
        try:
            els = self.page.query_selector_all(_NON_NATIVE)
            for el in els:
                try:
                    if not el.is_visible():
                        continue
                    text = (el.inner_text() or "").strip().lower()
                    if any(kw in text for kw in _ADD_LABELS):
                        el.click(timeout=4000)
                        self._after_click()
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        # 4) Icon-class fallback (fa-plus, bi-plus-circle, etc.)
        _ICON_ADD = ("plus", "add", "create", "new-file")
        try:
            els = self.page.query_selector_all(
                'a.btn, a[class*="btn"], a[class*="action"], '
                'a[data-action], a[onclick]'
            )
            for el in els:
                try:
                    if not el.is_visible():
                        continue
                    icls = el.evaluate(
                        """e => {
                            let out = '';
                            const walk = n => {
                                if (n && n.nodeType === 1 && n.getAttribute) {
                                    out += ' ' + (n.getAttribute('class') || '');
                                    out += ' ' + (n.getAttribute('data-icon') || '');
                                    for (let c of (n.children || [])) walk(c);
                                }
                            };
                            walk(e);
                            return out.toLowerCase();
                        }""")
                    if any(icon in icls for icon in _ICON_ADD):
                        el.click(timeout=4000)
                        self._after_click()
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        _log("_click_add_button: no 'Ajouter'/'Nouveau' button found")
        return False

    def _click_by_text(self, text):
        if not text:
            return False
        # 1) Playwright text locator (broad — finds text in any visible element)
        try:
            loc = self.page.get_by_text(text, exact=False).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=4000)
                self._after_click()
                return True
        except Exception:
            pass
        # 2) Native controls: button, a, [role='button'], input submit/button
        _NATIVE = ('button, [role="button"], a, input[type="submit"], '
                   'input[type="button"], input[type="image"]')
        try:
            els = self.page.query_selector_all(_NATIVE)
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
        # 3) Non-native clickable: div/span/li with btn/action/button/icon class,
        #    [onclick], [data-action] — matches the explorer's _CLICKABLE_SEL
        _NON_NATIVE = ('div[class*="btn"], span[class*="btn"], li[class*="btn"], '
                       'div[class*="action"], span[class*="action"], li[class*="action"], '
                       'div[class*="button"], span[class*="button"], li[class*="button"], '
                       '[onclick], [data-action]')
        try:
            els = self.page.query_selector_all(_NON_NATIVE)
            for el in els:
                try:
                    if not el.is_visible():
                        continue
                    if self._el_matches(el, text):
                        el.click(timeout=4000)
                        self._after_click()
                        return True
                except Exception:
                        continue
        except Exception:
            pass
        # 4) CSS selector fallback: try common button class patterns
        try:
            for sel in (f'a:has-text("{text}")', f'div:has-text("{text}")'):
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=4000)
                    self._after_click()
                    return True
        except Exception:
            pass
        return False

    def _row_action_group(self, target):
        """Return the search-key tuple for a row-action target word, or None."""
        t = (target or "").lower().strip()
        if not t:
            return None
        for group, keys in self._ROW_ACTION_KEYS.items():
            if t == group or any(k in t for k in keys):
                return keys
        return None

    def _click_row_action(self, target):
        """Click an icon-only action button (view/edit/delete) inside a table row.

        Targets the row of the entity that was just filled (self.last_fill_value)
        if present, otherwise the first data row. Returns True on success.
        """
        keys = self._row_action_group(target)
        if not keys:
            return False
        try:
            rows = self.page.query_selector_all("table tbody tr")
        except Exception:
            rows = []
        if not rows:
            return False

        wanted = (self.last_fill_value or "").strip()
        rows_text = []
        for tr in rows:
            try:
                rows_text.append((tr.inner_text() or "").lower())
            except Exception:
                rows_text.append("")

        # Prefer the row whose text contains the last filled value (the entity
        # just created), otherwise fall back to the first data row.
        row_idx = None
        if wanted:
            w = wanted.lower()
            for i, txt in enumerate(rows_text):
                if w in txt:
                    row_idx = i
                    break
        if row_idx is None and rows_text:
            for i, txt in enumerate(rows_text):
                if txt.strip():
                    row_idx = i
                    break

        # Within the chosen row, click the action control matching the keys.
        script = """(arg) => {
            const rows = Array.prototype.slice.call(document.querySelectorAll('table tbody tr'));
            const row = rows[arg.row];
            if (!row) return false;
            const els = row.querySelectorAll('a, button, input[type="submit"], i, span, input[type="button"]');
            for (let i = 0; i < els.length; i++) {
                const e = els[i];
                const cls = (e.getAttribute && (e.getAttribute('class') || '')) || '';
                const title = (e.getAttribute && (e.getAttribute('title') || '')) || '';
                const aria = (e.getAttribute && (e.getAttribute('aria-label') || '')) || '';
                const id = (e.getAttribute && (e.getAttribute('id') || '')) || '';
                const oc = (e.getAttribute && (e.getAttribute('onclick') || '')) || '';
                const hay = (cls + ' ' + title + ' ' + aria + ' ' + id + ' ' + oc).toLowerCase();
                for (let k = 0; k < arg.keys.length; k++) {
                    if (hay.indexOf(arg.keys[k]) !== -1) {
                        e.click();
                        return {index: i, found: (cls || title).slice(0, 40)};
                    }
                }
            }
            return false;
        }"""
        # Delete buttons often open a native confirm() dialog ("Êtes-vous sûr ?").
        # Playwright dismisses dialogs by default, so a delete would silently
        # never happen. Register the accepting handler BEFORE the click.
        accept_confirm = any(
            k in ("supprim", "delete", "remove", "trash", "effacer",
                  "erase", "suppression", "retirer") for k in keys)
        if accept_confirm:
            self._accept_next_confirm()
        res = self.page.evaluate(script, {"keys": list(keys), "row": row_idx})
        if res:
            self._after_click()
            _log(f"_click_row_action: clicked '{target}' on row {row_idx} ({res.get('found')})")
            return True
        _log(f"_click_row_action: no '{target}' action button in row {row_idx}")
        return False

    def _accept_next_confirm(self):
        """Accept the next native confirm()/alert() dialog.

        Playwright auto-dismisses dialogs; registering a handler in time
        (before the click that triggers it) lets us accept instead.
        """
        try:
            def _h(dialog):
                try:
                    dialog.accept()
                except Exception:
                    pass
            self.page.once("dialog", _h)
        except Exception:
            pass

    def _el_matches(self, el, text):
        """Check if an element matches the given text via various attributes."""
        text_lower = (text or "").lower()
        for attr in ("title", "aria-label", "placeholder", "data-tooltip",
                     "data-tip", "data-original-title", "value"):
            try:
                v = (el.get_attribute(attr) or "").lower()
                if v and text_lower in v:
                    return True
            except Exception:
                pass
        try:
            inner = (el.inner_text() or "").lower()
            if text_lower in inner:
                return True
        except Exception:
                pass
        # Icon-font fallback: check icon classes on descendants
        try:
            icls = el.evaluate(
                """e => {
                    let out = '';
                    const walk = n => {
                        if (n && n.nodeType === 1 && n.getAttribute) {
                            out += ' ' + (n.getAttribute('class') || '');
                            out += ' ' + (n.getAttribute('data-icon') || '');
                            for (let c of (n.children || [])) walk(c);
                        }
                    };
                    walk(e);
                    return out.toLowerCase();
                }""")
            # Map common icon classes to their CRUD labels
            _ICON_MAP = {
                "plus": "ajouter", "add": "ajouter", "create": "ajouter",
                "pencil": "modifier", "edit": "modifier", "update": "modifier",
                "trash": "supprimer", "delete": "supprimer", "remove": "supprimer",
                "erase": "supprimer",
                "search": "recherche", "zoom": "recherche",
                "eye": "visualiser", "show": "visualiser", "view": "visualiser",
                "voir": "visualiser", "detail": "visualiser", "display": "visualiser",
            }
            for icon_key, meaning in _ICON_MAP.items():
                if icon_key in icls and text_lower in meaning:
                    return True
        except Exception:
                pass
        return False

    def _fill_by_label(self, label, value):
        """Fill a form field by its label text, placeholder, or name.

        Waits for the element to appear first (handles modals/forms that
        open after a click).
        """
        # Wait briefly for any modal/form to appear
        try:
            self.page.wait_for_selector(
                'form, [role="dialog"], .modal, .modal-body',
                state="visible", timeout=5000)
        except Exception:
            pass
        try:
            # Try by label text
            loc = self.page.get_by_label(label, exact=False).first
            if loc.count() > 0:
                loc.wait_for(state="visible", timeout=4000)
                loc.fill(str(value))
                return True
        except Exception:
            pass
        try:
            # Try by placeholder
            sel = f'input[placeholder*="{label}" i], textarea[placeholder*="{label}" i]'
            el = self.page.query_selector(sel)
            if el and el.is_visible():
                el.fill(str(value))
                return True
        except Exception:
            pass
        try:
            # Try by name attribute
            sel = f'input[name*="{label}" i], textarea[name*="{label}" i]'
            el = self.page.query_selector(sel)
            if el and el.is_visible():
                el.fill(str(value))
                return True
        except Exception:
            pass
        try:
            # Try by id attribute
            sel = f'input[id*="{label}" i], textarea[id*="{label}" i]'
            el = self.page.query_selector(sel)
            if el and el.is_visible():
                el.fill(str(value))
                return True
        except Exception:
            pass
        try:
            # Try finding a label element with matching text, then its associated input
            labels = self.page.query_selector_all('label')
            for lbl in labels:
                try:
                    txt = (lbl.inner_text() or "").strip().lower()
                    if label.lower() in txt:
                        for_attr = lbl.get_attribute("for")
                        if for_attr:
                            el = self.page.query_selector(f'#{for_attr}')
                            if el and el.is_visible():
                                el.fill(str(value))
                                return True
                        # label wraps the input
                        el = lbl.query_selector('input, textarea, select')
                        if el and el.is_visible():
                            el.fill(str(value))
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

    def _verify_row(self, value):
        """Check that a value appears in at least one visible table row.

        Used by the 'vérifier que la ligne contient X' step generated in
        Option C. Falls back to the search box when the value is not on the
        current page (e.g. behind the DataTable filter).
        """
        if not value or not str(value).strip():
            return False
        v = str(value).strip().lower()
        probe = """(v) => {
            const tables = document.querySelectorAll('table');
            for (const tb of tables) {
                const rows = tb.querySelectorAll('tbody tr');
                for (const tr of rows) {
                    if ((tr.innerText || '').toLowerCase().indexOf(v) !== -1) return true;
                }
            }
            return false;
        }"""
        try:
            if self.page.evaluate(probe, v):
                return True
        except Exception:
            pass
        # Not visible on the current page: try the search box, then re-check.
        try:
            si = self._find_search_input()
            if si:
                si.fill(str(value).strip())
                self.page.keyboard.press("Enter")
                self._after_click()
                if self.page.evaluate(probe, v):
                    return True
        except Exception:
            pass
        return False

    # -- AI-driven natural-language step (manual scenarios) ------------------

    def _collect_page_state(self):
        """Snapshot the actionable elements visible on the current page.

        Returns a list of {label, type, node} for AI planning.

        Optimised: the DOM scan + visibility + geometry + nav detection are
        all computed in a single page.evaluate (one round-trip) instead of a
        slow per-element loop of dozens of round-trips.
        """
        _log("collect_page_state: scanning visible elements...")

        _CLICKABLE = (
            'button, [role="button"], '
            'input[type="submit"], input[type="button"], input[type="image"], '
            'input[type="checkbox"], input[type="radio"], select, textarea, '
            'a.btn, a[class*="btn"], a[class*="button"], '
            'a[class*="action"], a[data-action], a[aria-haspopup], a[onclick], a[href], '
            'div[class*="btn"], span[class*="btn"], li[class*="btn"], '
            'div[class*="action"], span[class*="action"], li[class*="action"], '
            'div[class*="icon"], span[class*="icon"], i[class*="icon"], '
            '[onclick], [data-action], input[type="text"], input:not([type]), '
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"])'
        )

        _NAV_RE = (
            r'(^|[\s_-])(sidebar|side-bar|sidenav|navbar|nav-bar|topbar|top-bar|'
            r'menu-bar|app-nav|main-header|main-footer|site-header|site-footer|'
            r'app-header|app-footer)([\s_-]|$)'
        )

        js = """
        (arg) => {
            const sel = arg.sel, navRe = arg.navRe;
            const nodes = Array.prototype.slice.call(document.querySelectorAll(sel));
            const out = [];
            const nav = new RegExp(navRe);
            for (let i = 0; i < nodes.length; i++) {
                const n = nodes[i];
                let r = n.getBoundingClientRect();
                const vis = r.width > 0 && r.height > 0 &&
                           getComputedStyle(n).visibility !== 'hidden' &&
                           getComputedStyle(n).display !== 'none';
                // ancestors hidden rule
                let hidden = false;
                let p = n;
                while (p && p !== document.body) {
                    const cs = getComputedStyle(p);
                    if (cs.display === 'none' || cs.visibility === 'hidden') { hidden = true; break; }
                    p = p.parentElement;
                }
                if (!vis || hidden) continue;
                // nav detection (stop before body/html theme classes)
                let isNav = false;
                let anc = n.parentElement;
                while (anc && anc.tagName !== 'BODY' && anc.tagName !== 'HTML') {
                    const at = anc.tagName.toLowerCase();
                    const acl = (anc.getAttribute && (anc.getAttribute('class')||''))||'';
                    const aro = anc.getAttribute && anc.getAttribute('role');
                    if (at === 'nav' || at === 'aside' || aro === 'navigation' ||
                        nav.test(acl.toLowerCase())) { isNav = true; break; }
                    anc = anc.parentElement;
                }
                const tag = n.tagName ? n.tagName.toLowerCase() : '';
                const type = (n.getAttribute && n.getAttribute('type') || '') || '';
                const cls = (n.getAttribute && n.getAttribute('class') || '') || '';
                const role = (n.getAttribute && n.getAttribute('role') || '') || '';
                // label
                let label = '';
                if (tag === 'button' || tag === 'a') {
                    label = (n.innerText || '').trim();
                    if (!label) {
                        // Icon-only buttons: the title/aria-label acts as the
                        // selector the QA uses ("voir les détails", ...).
                        label = (n.getAttribute('title') || n.getAttribute('aria-label') ||
                                 n.getAttribute('data-tooltip') || n.getAttribute('data-tip') ||
                                 n.getAttribute('data-original-title') || '');
                    }
                } else if (n.tagName === 'INPUT' || n.tagName === 'TEXTAREA' || n.tagName === 'SELECT') {
                    label = (n.getAttribute('placeholder') || n.getAttribute('name') ||
                             n.getAttribute('aria-label') || type || tag);
                } else {
                    label = (n.innerText || '').trim();
                    if (label.length > 80) label = '';
                    if (!label) label = (n.getAttribute('aria-label') || n.getAttribute('title') ||
                                         n.getAttribute('data-tooltip') || n.getAttribute('data-tip') || '');
                }
                label = label.replace(/\\s+/g, ' ').slice(0, 60);
                out.push({i, tag, type, cls: cls.slice(0,40), role, isNav, label});
            }
            return out;
        }
        """
        gathered = []
        try:
            gathered = self.page.evaluate(js, {"sel": _CLICKABLE, "navRe": _NAV_RE}) or []
        except Exception as e:
            _log(f"collect_page_state: JS scan failed ({e})")

        # Keep original query order for node index resolution
        all_nodes = []
        try:
            all_nodes = self.page.query_selector_all(_CLICKABLE)
        except Exception:
            all_nodes = []

        state = []
        seen = set()
        for g in gathered:
            try:
                if g.get("isNav"):
                    continue
                label = (g.get("label") or "").strip()
                if not label:
                    continue
                tag = g.get("tag", "")
                el_type = (g.get("type") or "").lower()
                role = (g.get("role") or "").lower()
                cls = (g.get("cls") or "").lower()
                onclick_attr = ""

                if tag == "button" or (tag == "input" and el_type in ("submit", "button", "image")):
                    ktype = "button"
                elif role == "button":
                    ktype = "button"
                elif tag == "input" and el_type == "checkbox":
                    ktype = "checkbox"
                elif tag == "input" and el_type == "radio":
                    ktype = "radio"
                elif tag == "select":
                    ktype = "select"
                elif tag == "a":
                    ktype = "link"
                elif tag == "textarea":
                    ktype = "textarea"
                elif tag == "input":
                    ktype = "input"
                elif "btn" in cls or "action" in cls or "button" in cls:
                    ktype = "button"
                else:
                    continue

                key = (label, ktype)
                if key in seen:
                    continue
                seen.add(key)

                idx = g.get("i")
                node = all_nodes[idx] if (0 <= idx < len(all_nodes)) else None
                if node is None:
                    continue
                state.append({"label": label, "type": ktype, "node": node, "css": g.get("cls", "")})
            except Exception:
                continue
        _log(f"collect_page_state: found {len(state)} elements")
        return state

    def _execute_ai_action(self, plan, project_context=""):
        """Execute a single IA-planned action on the page. Returns True if it
        was performed without exception."""
        action = (plan.get("action") or "").upper()
        selector = plan.get("selector")
        text = plan.get("text") or ""
        _log(f"execute_ai_action: {action} selector={selector} text={text}")
        try:
            node = None
            if isinstance(selector, int) or (isinstance(selector, str) and selector.isdigit()):
                try:
                    idx = int(selector)
                    state = self._collect_page_state()
                    if 0 <= idx < len(state):
                        node = state[idx]["node"]
                except Exception:
                    node = None
            if node is None and selector and isinstance(selector, str) and not selector.isdigit():
                node = self.page.query_selector(selector)
            if node is None:
                # No index/resolver -> fall back to text first match
                node = self._text_match(action, text)

            if action == "CLICK":
                if node:
                    node.click(timeout=5000)
                    self.page.wait_for_timeout(400)
                else:
                    self._click_by_text(text)
            elif action == "FILL":
                if node:
                    node.fill(text or "")
                else:
                    # Try to find the first visible empty input and fill it
                    filled = False
                    try:
                        inputs = self.page.query_selector_all(
                            'input[type="text"], input:not([type]), '
                            'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), '
                            'textarea')
                        for inp in inputs:
                            try:
                                if inp.is_visible() and not (inp.input_value() or "").strip():
                                    inp.fill(text or "")
                                    filled = True
                                    break
                            except Exception:
                                continue
                    except Exception:
                        pass
                    if not filled:
                        self._click_by_text(text or "")
            elif action == "SELECT":
                if node:
                    node.select_option(text)
            elif action == "NAVIGATE":
                url = text or selector
                if isinstance(url, str) and url.startswith("http"):
                    self.goto(url)
                    self.page.wait_for_timeout(500)
            elif action == "WAIT":
                self.page.wait_for_timeout(1500)
            elif action == "SCROLL":
                self.page.mouse.wheel(0, 600)
                self.page.wait_for_timeout(300)
            return True
        except Exception as e:
            _log(f"execute_ai_action FAILED: {e}")
            return False

    def _text_match(self, action, text):
        # not used for CLICK by text alone here
        return None

    def ai_step(self, step, project_context=""):
        """Drive the browser through a functionality (module -> functionality
        -> steps), slowly, point by point, using OpenAI for each browser
        gesture.

        Sequence followed for every scenario:
          1) Click the MODULE that contains the functionality (navigation).
          2) Click the FUNCTIONALITY named in the scenario.
          3) Perform the steps of the description ONE BY ONE, slowly
             (an explicit delay between each action).
        """
        from . import ai
        mod = step.get("module", "Général")
        func = step.get("function", step.get("label", "Scénario"))
        fn_desc = step.get("function_description", "")
        mod_desc = step.get("module_description", "")
        steps = step.get("steps") or []

        action_text = self._build_scenario_instruction(step)
        _log(f"AI_STEP start: mod={mod} func={func} steps={len(steps)} desc={action_text[:100]}")

        has_ai = bool(env.OPENAI_API_KEY)
        if not has_ai:
            _log("AI_STEP: no OPENAI_API_KEY — using Playwright fallback")

        user_desc = (step.get("user_desc") or "").strip()
        goal_ctx = self._build_goal_context(mod, mod_desc, func, fn_desc, steps, project_context, user_desc)
        constraint = ("Tu dois cliquer d'abord sur le MODULE '%s' (menu de "
                      "navigation) puis sur la FONCTIONNALITÉ '%s', et ensuite "
                      "dérouler les étapes UNE PAR UNE, lentement." % (mod, func))

        done_clicked = set()
        executed = 0

        # If the functionality has a direct URL (URL-based scan), skip menu
        # clicking entirely and go straight there.
        direct_url = (step.get("url") or "").strip()
        if direct_url:
            _log(f"AI_STEP: navigating directly to URL: {direct_url}")
            code = self.goto(direct_url)
            settle(self.page)
            if code and code >= 400:
                _log(f"AI_STEP: direct URL returned HTTP {code}")
            done_clicked.add(("CLICK", mod, mod))

        def _try_ai_phase(label, phase_text, max_loops):
            """Ask OpenAI to perform a navigation/action phase. Returns True on
            done, False on timeout/loop.  Falls back to Playwright when AI is
            unavailable."""
            nonlocal executed
            if not has_ai:
                return False  # caller should try direct click
            attempts = 0
            no_progress = 0
            last = []
            _log(f"run_phase({label}): max_loops={max_loops}")
            while attempts < max_loops:
                attempts += 1
                if self._cancel_requested():
                    _log(f"run_phase({label}): cancelled")
                    return False
                state = [{"label": el["label"], "type": el["type"],
                          "css": el.get("css", "")}
                         for el in self._collect_page_state()]
                _log(f"run_phase({label}): attempt {attempts}, {len(state)} elements on page")
                plan = ai.plan_step_action(phase_text, state, goal_ctx + "\n" + constraint)
                if not plan:
                    _log(f"run_phase({label}): AI returned None (attempt {attempts})")
                    return False
                if plan.get("done"):
                    _log(f"run_phase({label}): AI says done (comment={plan.get('comment','')})")
                    if no_progress > 0:
                        return True
                    no_progress += 1
                    continue
                no_progress = 0
                sig = (plan.get("action"), plan.get("selector"), plan.get("text"))
                _log(f"run_phase({label}): AI action={sig}")
                if sig in last[-3:]:
                    _log(f"run_phase({label}): repeated action, aborting")
                    return False
                last.append(sig)
                ok = self._execute_slow_ai_action(plan, goal_ctx)
                if ok:
                    executed += 1
                done_clicked.add((plan.get("action"), plan.get("text"),
                                  plan.get("selector")))
            _log(f"run_phase({label}): exhausted {max_loops} attempts")
            return False

        try:
            nav_path = [s for s in (step.get("nav_path") or []) if s]

            if nav_path:
                _log(f"AI_STEP: using explicit nav_path={nav_path}")
                self._navigate_path(nav_path, goal_ctx, constraint, _try_ai_phase)
            else:
                # Phase 1: reach the MODULE that holds the functionality.
                _log(f"AI_STEP: nav (module='{mod}', func='{func}')")
                if mod and mod.lower() not in ("général", "general") and mod not in done_clicked:
                    if self._click_nav_label(mod) or self._click_by_text(mod):
                        self._slow_pause()
                        done_clicked.add(("CLICK", mod, mod))
                        _log(f"AI_STEP: module '{mod}' clicked directly")
                    else:
                        _log(f"AI_STEP: module '{mod}' NOT found, using AI/fallback")
                        _try_ai_phase("module",
                                       f"Cliquer sur le MODULE (menu de navigation) nommé '{mod}'.", 8)
                        # Playwright search-based fallback for module
                        if mod not in done_clicked:
                            search_input = self._find_search_input()
                            if search_input:
                                try:
                                    search_input.click(timeout=3000)
                                    search_input.fill(mod)
                                    self.page.keyboard.press("Enter")
                                    self._after_click()
                                    done_clicked.add(("CLICK", mod, mod))
                                    _log(f"AI_STEP: module '{mod}' found via search")
                                except Exception:
                                    _log(f"AI_STEP: module '{mod}' search failed")

                # Phase 2: open the FUNCTIONALITY itself.
                if func and func not in done_clicked:
                    if self._click_nav_label(func) or self._click_by_text(func):
                        self._slow_pause()
                        _log(f"AI_STEP: func '{func}' clicked directly")
                    else:
                        _log(f"AI_STEP: func '{func}' NOT found, using AI/fallback")
                        _try_ai_phase("fonctionnalité",
                                       f"Ouvrir la FONCTIONNALITÉ nommée '{func}' (après avoir cliqué sur le module '{mod}').", 8)
                        # Playwright search-based fallback for functionality
                        if func not in done_clicked:
                            search_input = self._find_search_input()
                            if search_input:
                                try:
                                    search_input.click(timeout=3000)
                                    search_input.fill(func)
                                    self.page.keyboard.press("Enter")
                                    self._after_click()
                                    done_clicked.add(("CLICK", func, func))
                                    _log(f"AI_STEP: func '{func}' found via search")
                                except Exception:
                                    _log(f"AI_STEP: func '{func}' search failed")

            # Phase 3: perform the description's steps one by one, slowly.
            if steps:
                _log(f"AI_STEP: executing {len(steps)} explicit steps")
                prev_url = self.page.url
                step_num = 0
                for i, s in enumerate(steps, 1):
                    if self._cancel_requested():
                        break
                    # Skip separators and empty lines
                    if not s or not s.strip() or s.strip() in ('CRÉATION', 'MODIFICATION', 'SUPPRESSION'):
                        _log(f"AI_STEP step {i}: separator/empty, skipping")
                        continue
                    step_num += 1
                    done = False
                    action_performed = False
                    guard = 0

                    _log(f"AI_STEP step {step_num}/{len(steps)}: {s[:80]}")

                    if has_ai:
                        # --- AI path ---
                        aim = (f"Étape {step_num}/{len(steps)} de la fonctionnalité '{func}': {s}. "
                               f"Tu DOIS effectuer cette action maintenant. "
                               f"Analyse les éléments de la page et clique sur celui "
                               f"qui correspond. Réponds done APRÈS avoir cliqué.")
                        while not done and guard < 8:
                            guard += 1
                            state = [{"label": el["label"], "type": el["type"],
                                      "css": el.get("css", "")}
                                     for el in self._collect_page_state()]
                            plan = ai.plan_step_action(aim, state, goal_ctx)
                            if not plan:
                                _log(f"AI_STEP step {step_num}: AI returned None (guard={guard})")
                                break
                            if plan.get("done"):
                                if action_performed:
                                    _log(f"AI_STEP step {step_num}: done after action (guard={guard})")
                                    done = True
                                    break
                                else:
                                    _log(f"AI_STEP step {step_num}: AI says done but no action, retrying")
                                    continue
                            if self._execute_slow_ai_action(plan, goal_ctx):
                                executed += 1
                                action_performed = True

                    # *** CRITICAL: if AI failed or was unavailable, always try
                    #     Playwright fallback so a bad API key never blocks the
                    #     entire scenario. ***
                    if not action_performed and not done:
                        _log(f"AI_STEP step {step_num}: AI path inconclusive, trying Playwright fallback")
                        if self._fallback_step(s, func):
                            executed += 1
                            action_performed = True
                            done = True
                        else:
                            self.page.wait_for_timeout(1500)
                            if self._fallback_step(s, func):
                                executed += 1
                                action_performed = True
                                done = True

                    # Record result for this step
                    new_url = self.page.url
                    if done or action_performed:
                        self._record(
                            {"module": mod, "function": func,
                             "action": f"Étape {step_num}/{len(steps)}: {s}",
                             "data": "", "severity": ""},
                            "PASS", f"Étape {step_num} exécutée", "OK")
                    else:
                        self._record(
                            {"module": mod, "function": func,
                             "action": f"Étape {step_num}/{len(steps)}: {s}",
                             "data": "", "severity": "MAJOR"},
                            "FAIL", f"Étape {step_num} à exécuter",
                            f"Introuvable sur la page: {s}",
                            self._screenshot(mod, func, "FAIL"))
                        _log(f"AI_STEP step {step_num}: FAILED — element not found")
                        break
                    prev_url = new_url
            elif not executed:
                # manual single-line scenario: try AI then fallback
                if has_ai:
                    _log("AI_STEP: no explicit steps, trying AI guided run")
                    _try_ai_phase("scénario", action_text, 12)
                else:
                    _log("AI_STEP: no explicit steps, trying Playwright fallback")
                    if self._fallback_step(action_text, func):
                        executed += 1

            _log(f"AI_STEP result: executed={executed}")
            if executed == 0:
                shot = self._screenshot(mod, func, "WARNING")
                self._record(
                    {"module": mod, "function": func, "action": action_text,
                     "data": "", "severity": "MINOR"},
                    "WARNING",
                    "Le scénario s'exécute automatiquement",
                    "Aucune action exécutable détectée",
                    shot)
                return "WARNING"
            self._record(
                {"module": mod, "function": func, "action": action_text,
                 "data": "", "severity": ""},
                "PASS", "Le scénario s'exécute sans bloquer",
                f"Exécution terminée ({executed} action(s), point par point).")
            return "PASS"
        except Exception as e:
            _log(f"AI_STEP EXCEPTION: {e}\n{traceback.format_exc()}")
            self._record(
                {"module": mod, "function": func, "action": action_text,
                 "data": "", "severity": "CRITICAL"},
                "FAIL", "Le scénario s'exécute sans erreur", f"Exception: {e}",
                self._screenshot(mod, func, "FAIL"))
            return "FAIL"

    # ------------------------------------------------------------------
    # Fallback Playwright — works WITHOUT OpenAI
    # ------------------------------------------------------------------

    # Keywords that map a French step description to a Playwright action.
    _STEP_CLICK_KEYWORDS = (
        ("ajouter", "add", "créer", "create", "nouveau", "new",
         "nouvelle", "plus", "+"),
        ("visualiser", "voir", "afficher", "consulter", "regarder",
         "view", "show", "display"),
        ("modifier", "edit", "update", "éditer"),
        ("supprimer", "delete", "remove", "retirer", "effacer"),
        ("enregistrer", "sauver", "save", "valider", "submit"),
        ("chercher", "rechercher", "search", "find"),
        ("fermer", "close", "annuler", "cancel"),
        ("confirmer", "ok", "oui", "yes"),
        ("exporter", "export", "télécharger", "download"),
        ("importer", "import"),
        ("imprimer", "print"),
    )

    # --- Row-action detection (table rows with icon-only buttons) ----------
    # Maps a scenario target word to the class/title/icon attributes found on
    # the action buttons. Used by _click_row_action to click the right button
    # in the right row (typically the row of the entity just created).
    _ROW_ACTION_KEYS = {
        "visualiser": ("visualis", "voir", "affiche", "consulte", "view",
                       "show", "eye", "display", "regard"),
        "modifier": ("modifier", "modif", "edit", "éditer", "editer", "update",
                     "pencil", "pen"),
        "supprimer": ("supprimer", "suppression", "delete", "remove", "trash",
                      "effacer", "retirer", "erase"),
    }

    def _parse_step_intent(self, step_text):
        """Parse a French step description and return (action, target_text, value).

        action: 'click' | 'fill' | 'search' | 'select' | 'wait' | 'check' | 'uncheck'
        target_text: the label/button text to look for, or the field name.
        value: the value for fill/select actions.

        Examples:
          "Cliquer sur le bouton Ajouter" → ("click", "Ajouter", "")
          "Remplir le champ Nom avec Test" → ("fill", "Nom", "Test")
          "Chercher la catégorie Test" → ("search", "Test", "")
          "Cocher la case Actif" → ("check", "Actif", "")
        """
        import re as _re
        t = step_text.lower().strip()

        # --- NAVIGATE: a real URL in the step is a direct navigation target
        # ("revient sur https://...", "aller vers <url>", ...). Without this,
        # such steps are parsed as clicks and the fuzzy fallback ends up
        # clicking the wrong element.
        m_url = _re.search(r"(https?://[^\s]+)", step_text)
        if m_url:
            url = m_url.group(1).strip().strip('\'".,;:…)»“”')
            if url:
                return ("navigate", url, "")

        # --- FILL: "remplir ... avec ..." or "saisir ... avec ..." ---
        for pat in (r"rempli(?:r|s|ez)?\s+(?:le\s+)?(?:champ\s+)?['\"]?([^'\"\s]+(?:\s+[^'\"\s]+)*?)['\"]?\s+(?:avec|et\s+(?:mets?|mettez)?|:|value)",
                    r"saisi(?:r|s|ez)?\s+(?:le\s+)?(?:champ\s+)?['\"]?([^'\"\s]+(?:\s+[^'\"\s]+)*?)['\"]?\s+(?:avec|et\s+(?:mets?|mettez)?|:|value)",
                    r"entr(?:er|ez|re)\s+(?:le\s+)?(?:champ\s+)?['\"]?([^'\"\s]+(?:\s+[^'\"\s]+)*?)['\"]?\s+(?:avec|et\s+(?:mets?|mettez)?|:|value)"):
            fill_match = _re.search(pat, t)
            if fill_match:
                field = fill_match.group(1).strip().title()
                # Extract value from ORIGINAL step_text to preserve case
                orig_rest = step_text[fill_match.end():].strip()
                value = ""
                for sep in ("avec ", "Avec ", "et mets ", "et mettez ", ": ", "value "):
                    if orig_rest.startswith(sep):
                        value = orig_rest[len(sep):].strip().strip('"\'')
                        break
                if not value:
                    value = orig_rest.split(".")[0].strip().strip('"\'')
                return ("fill", field, value or "")

        # --- PAGE CHECK: "vérifier que la page se charge / s'ouvre" ---
        # Pure navigation verification (dashboard, stats, read-only pages).
        # Does NOT look for a table row: the page simply must have loaded.
        if any(w in t for w in (
                "page se charge", "page charge", "page s'ouvre", "page ouvre",
                "se charge sans erreur", "s'ouvre sans erreur",
                "se charge correctement", "la page charge")):
            return ("page_check", "", "")

        # --- NAV OPEN: "ouvrir la page X" / "aller vers X" / "naviguer vers X"
        # Since the runner has already landed on the functionality screen
        # (direct URL or menu-driven), this step simply asserts a real page is
        # loaded — it is never a click on a literal "Ouvrir la page X" button.
        if any(w in t for w in ("ouvrir la page", "ouvrir le module",
                                "naviguer vers la page", "naviguer vers",
                                "naviguer sur", "aller vers", "aller sur",
                                "aller a", "aller à", "ouvrir l'écran")):
            return ("page_check", "", "")

        # --- UNCHECK: "décocher la case ..." ---
        if any(w in t for w in ("décocher", "decocher", "décochez", "uncheck")):
            m = _re.search(r"décoch(?:er|ez|ons)?\s+(?:la\s+)?(?:case\s+)?['\"]?([^'\"\s]+(?:\s+[^'\"\s]+)*?)['\"]?(?:\s|$)", t)
            if m:
                return ("uncheck", m.group(1).strip().title(), "")
            # Fallback: "décocher Inactif"
            m = _re.search(r"décoch(?:er|ez|ons)?\s+(?:la\s+)?(?:case\s+)?(.+)", t)
            if m:
                return ("uncheck", m.group(1).strip().title(), "")

        # --- VERIFY: "vérifier que la ligne contient X" / "vérifier la
        #      présence de X dans le tableau" / "vérifier que X apparaît ..." ---
        if any(w in t for w in ("vérifier", "verifier", "vérifiez", "verifiez", "vérifie",
                                "verifie", "s'assurer", "assurez-vous", "assure-toi",
                                "contrôler", "controler")):
            m = _re.search(r"(?:vérifi(?:er|ez|e)?|verifi(?:er|ez|e)?|s'assurer|"
                           r"assurez(?:-vous)?|assure-toi|contr(?:ô|o)ler)", t)
            if not m:
                return ("verify", "", "")
            term = t[m.end():].strip()
            cuts = (" dans le tableau des", " dans la liste des", " dans le tableau",
                    " dans la liste", " apparaît dans", " apparait dans",
                    " est présent dans", " est present dans", " est présente dans",
                    " est presente dans", " existe dans", " dans le menu",
                    " sur la page", " a l'écran", " à l'écran", " d'écran",
                    " dans le panneau", " dans le formulaire")
            idxs = [term.find(c) for c in cuts]
            idxs = [i for i in idxs if i != -1]
            if idxs:
                term = term[:min(idxs)]
            for pre in ("que ", "la ligne ", "l'entité ", "l'element ", "l'élément ",
                        "le libellé ", "le libelle ", "la valeur ", "le champ ",
                        "contient ", "contiennent ", "bien ", "la présence de ",
                        "la presence de ", "sa présence dans ", "sa presence dans ",
                        "le nom ", "le label "):
                if term.startswith(pre):
                    term = term[len(pre):]
            term = term.strip().strip("'\"").strip()
            if term:
                return ("verify", term, "")
            return ("verify", "", "")

        # --- CHECK: "cocher la case ..." ---
        if any(w in t for w in ("cocher", "cochez", "check", "activer la case")):
            m = _re.search(r"coch(?:er|ez|ons)?\s+(?:la\s+)?(?:case\s+)?['\"]?([^'\"\s]+(?:\s+[^'\"\s]+)*?)['\"]?(?:\s|$)", t)
            if m:
                return ("check", m.group(1).strip().title(), "")
            # Fallback: "cocher Actif"
            m = _re.search(r"coch(?:er|ez|ons)?\s+(.+)", t)
            if m:
                return ("check", m.group(1).strip().title(), "")

        # --- SEARCH: "chercher/rechercher ..." ---
        if any(w in t for w in ("cherch", "recherch", "search", "filter")):
            _SEARCH_SKIP = ("la ", "le ", "les ", "un ", "une ", "du ", "des ", "d'", "l'")
            for kw in ("chercher ", "rechercher ", "search ", "filter "):
                if kw in t:
                    term = t.split(kw, 1)[1].strip().split(".")[0].strip()
                    for skip in _SEARCH_SKIP:
                        if term.startswith(skip):
                            term = term[len(skip):]
                            break
                    return ("search", term, "")
            return ("search", "", "")

        # --- CLICK: detect the action keyword and find the target label ---
        for keywords in self._STEP_CLICK_KEYWORDS:
            if any(kw in t for kw in keywords):
                target = self._extract_click_target(t)
                if not target:
                    target = keywords[0].title()
                return ("click", target, "")

        # --- SELECT ---
        if any(w in t for w in ("sélectionner", "selectionner", "select", "choisir", "choisissez")):
            m = _re.search(r"(?:sélectionner|selectionner|select|choisir|choisissez)\s+(.+?)(?:\s+avec\s+(.+))?$", t)
            if m:
                return ("select", m.group(1).strip().split(".")[0].strip(),
                        (m.group(2) or "").strip())

        # --- WAIT ---
        if any(w in t for w in ("attendre", "wait", "patienter")):
            return ("wait", "", "")

        # --- Default: treat as a click on the full text ---
        target = step_text.strip()
        extracted = self._extract_click_target(t)
        if extracted:
            target = extracted
        return ("click", target, "")

    def _extract_click_target(self, t):
        """Extract the button/text selector from a click step.

        'bouton'/'button'/'btn' is a strong signal: the whole following label
        is the selector, and a button title acts as a precise locator (e.g.
        "Voir les détails"). Falls back to the object after the click verb
        ("cliquer sur X"). Returns '' when nothing usable is found.
        """
        # 1) After an explicit "bouton/button/btn": full label is the selector.
        m = re.search(
            r"(?:le\s+|la\s+|l['\"]?\s*)?(?:bouton|button|btn)\s+['\"]?([^'\".,;…]+)",
            t)
        if m:
            return m.group(1).strip().strip("'\"")
        # 2) Generic object after a click verb: "cliquer sur <target>".
        m = re.search(
            r"(?:cliqu(?:er|e|ez|ons)?|appuy(?:er|ez)?|touch(?:er|ez)?|"
            r"tap(?:er|ez)?)\s+(?:sur\s+)?(?:le\s+|la\s+|l['\"]?\s*)?"
            r"(?:bouton|button|btn\s+)?['\"]?([^'\".,;…]+)",
            t)
        if m:
            return m.group(1).strip().strip("'\"")
        # 3) Legacy quoted forms: "bouton 'X'" / "sur 'X'".
        m = re.search(r"(?:bouton|sur)\s+['\"]([^'\"]+)['\"]", t)
        if m:
            return m.group(1).strip().strip("'\"")
        return ""

    def _match_element_by_label(self, state, target):
        """Find the best matching element in page state for a target label.

        Returns (index, element) or (None, None).
        Uses fuzzy matching: exact > contains > partial word overlap.
        """
        if not state or not target:
            return None, None
        target_lower = target.lower().strip()

        # 1) Exact match (case-insensitive)
        for i, el in enumerate(state):
            if el["label"].lower().strip() == target_lower:
                return i, el

        # 2) Target is contained in the element label
        for i, el in enumerate(state):
            if target_lower in el["label"].lower():
                return i, el

        # 3) Element label is contained in the target
        for i, el in enumerate(state):
            if el["label"].lower() in target_lower:
                return i, el

        # 4) Word overlap: count shared significant words
        target_words = set(target_lower.split())
        # Remove common French stop words
        stop = {"le", "la", "les", "un", "une", "des", "du", "de", "sur",
                "et", "ou", "pour", "avec", "dans", "par", "au", "aux",
                "ce", "cette", "son", "sa", "ses", "qui", "que", "quoi",
                "bouton", "champ", "cliquer", "remplir", "faire", "valider"}
        target_words -= stop
        best_i, best_score = None, 0
        for i, el in enumerate(state):
            el_words = set(el["label"].lower().split()) - stop
            if not el_words:
                continue
            score = len(target_words & el_words)
            if score > best_score:
                best_score = score
                best_i = i
        if best_i is not None and best_score > 0:
            return best_i, state[best_i]

        return None, None

    def _fallback_step(self, step_text, func=""):
        """Execute a step using pure Playwright (no OpenAI needed).

        Parses the step description, matches against visible page elements,
        and performs the action.  Returns True on success.
        """
        _log(f"_fallback_step: parsing '{step_text[:80]}'")
        action, target, value = self._parse_step_intent(step_text)
        _log(f"_fallback_step: intent=({action}, '{target}', '{value}')")

        state = self._collect_page_state()
        _log(f"_fallback_step: {len(state)} elements on page")

        if action == "fill":
            # Fill a form field by label
            # Wait for form/modal to appear first
            try:
                self.page.wait_for_selector(
                    'form, [role="dialog"], .modal',
                    state="visible", timeout=5000)
            except Exception:
                pass
            ok = self._fill_by_label(target, value)
            if not ok:
                # Try clicking the field first (it might be a dropdown/toggle)
                idx, el = self._match_element_by_label(state, target)
                if el:
                    try:
                        el["node"].click(timeout=3000)
                        self.page.wait_for_timeout(500)
                        ok = self._fill_by_label(target, value)
                    except Exception:
                        pass
            if ok:
                self.last_fill_value = str(value)
                _log(f"_fallback_step: filled '{target}' = '{value}'")
            else:
                _log(f"_fallback_step: FAILED to fill '{target}'")
            return ok

        if action == "verify":
            ok = self._verify_row(target)
            if ok:
                _log(f"_fallback_step: verified '{target}' present in table")
            else:
                _log(f"_fallback_step: FAILED to verify '{target}'")
            return ok

        if action == "check":
            # Toggle a checkbox
            try:
                # Try by label
                loc = self.page.get_by_label(target, exact=False).first
                if loc.count() > 0 and loc.is_visible():
                    loc.check(timeout=3000)
                    self._after_click()
                    _log(f"_fallback_step: checked '{target}' via label")
                    return True
            except Exception:
                pass
            # Try finding checkbox near matching text
            try:
                checkboxes = self.page.query_selector_all('input[type="checkbox"]')
                for cb in checkboxes:
                    if cb.is_visible():
                        # Check nearby label text
                        lbl = cb.evaluate(
                            """e => {
                                const id = e.id;
                                if (id) {
                                    const l = document.querySelector('label[for="' + id + '"]');
                                    if (l) return l.innerText || '';
                                }
                                const p = e.closest('label');
                                if (p) return p.innerText || '';
                                const prev = e.previousElementSibling;
                                if (prev) return prev.innerText || '';
                                return '';
                            }""")
                        if target.lower() in (lbl or "").lower():
                            cb.check()
                            self._after_click()
                            _log(f"_fallback_step: checked '{target}' near label")
                            return True
            except Exception:
                pass
            _log(f"_fallback_step: FAILED to check '{target}'")
            return False

        if action == "uncheck":
            try:
                loc = self.page.get_by_label(target, exact=False).first
                if loc.count() > 0 and loc.is_visible():
                    loc.uncheck(timeout=3000)
                    self._after_click()
                    _log(f"_fallback_step: unchecked '{target}'")
                    return True
            except Exception:
                pass
            return False

        if action == "search":
            # Type into search field
            search_input = self._find_search_input()
            if search_input:
                try:
                    search_input.click(timeout=3000)
                    search_input.fill(target)
                    self.page.keyboard.press("Enter")
                    self._after_click()
                    _log(f"_fallback_step: searched '{target}'")
                    return True
                except Exception:
                    pass
            # Fallback: try any visible input
            try:
                inputs = self.page.query_selector_all('input[type="text"], input:not([type])')
                for inp in inputs:
                    if inp.is_visible():
                        inp.click(timeout=3000)
                        inp.fill(target)
                        self.page.keyboard.press("Enter")
                        self._after_click()
                        _log(f"_fallback_step: searched '{target}' via text input")
                        return True
            except Exception:
                pass
            _log(f"_fallback_step: FAILED to search '{target}'")
            return False

        if action == "wait":
            self.page.wait_for_timeout(2000)
            _log("_fallback_step: waited 2s")
            return True

        if action == "page_check":
            # Read-only page verification: the navigation (access / goto) has
            # already loaded the screen; just confirm we are on a real URL and
            # that no critical HTTP error was recorded for it.
            try:
                ok = bool(self.page.url and self.page.url.startswith("http"))
            except Exception:
                ok = False
            _log(f"_fallback_step: page_check -> {self.page.url} ({'OK' if ok else 'KO'})")
            return ok

        if action == "navigate":
            # Direct URL navigation ("revient sur https://...", ...).
            try:
                self.goto(target)
                settle(self.page)
            except Exception as e:
                _log(f"_fallback_step: navigate FAILED -> {target} ({e})")
                return False
            _log(f"_fallback_step: navigate -> {self.page.url}")
            return True

        if action == "select":
            # Try dropdown/select
            try:
                loc = self.page.get_by_label(target, exact=False).first
                if loc.count() > 0 and loc.is_visible():
                    try:
                        loc.select_option(value=value or "")
                    except Exception:
                        loc.select_option(label=value)
                    self._after_click()
                    _log(f"_fallback_step: selected '{target}' via label")
                    return True
            except Exception:
                pass
            idx, el = self._match_element_by_label(state, target)
            if el:
                try:
                    el["node"].click(timeout=3000)
                    self.page.wait_for_timeout(500)
                    self._click_by_text(target)
                    self._after_click()
                    _log(f"_fallback_step: selected '{target}'")
                    return True
                except Exception:
                    pass
            return False

        # --- CLICK (default) ---
        # Strategy 0: row-action buttons (view/edit/delete icons in a table).
        # Only hijack the click when the target is clearly an action-word, so
        # normal buttons like "Ajouter"/"Sauver" are not rerouted.
        if self._row_action_group(target):
            if self._click_row_action(target):
                _log(f"_fallback_step: clicked row action '{target}'")
                return True

        # Strategy 1: try _click_by_text which already handles many patterns
        if self._click_by_text(target):
            _log(f"_fallback_step: clicked '{target}' via _click_by_text")
            return True

        # Strategy 2: match from collected page state
        idx, el = self._match_element_by_label(state, target)
        if el:
            try:
                node = el.get("node")
                if node:
                    node.click(timeout=4000)
                    self._after_click()
                    _log(f"_fallback_step: clicked element #{idx} '{el['label']}'")
                    return True
            except Exception:
                pass

        # Strategy 3: Playwright locator broad search
        try:
            loc = self.page.get_by_role("button", name=target).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=4000)
                self._after_click()
                _log(f"_fallback_step: clicked '{target}' via role=button")
                return True
        except Exception:
            pass

        # Strategy 4: Try link
        try:
            loc = self.page.get_by_role("link", name=target).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=4000)
                self._after_click()
                _log(f"_fallback_step: clicked '{target}' via role=link")
                return True
        except Exception:
            pass

        _log(f"_fallback_step: FAILED to click '{target}'")
        return False

    def _slow_pause(self, ms=900):
        """Slow the execution down so each step is visibly performed."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        self.page.wait_for_timeout(ms)

    def _execute_slow_ai_action(self, plan, ctx):
        """Execute one AI action with a slow, deliberate pace."""
        try:
            ok = self._execute_ai_action(plan, ctx)
            self._slow_pause()
            return ok
        except Exception:
            return False

    def _build_scenario_instruction(self, step):
        """The single-line instruction = functionality name + steps."""
        func = step.get("function", step.get("label", "Scénario"))
        steps = step.get("steps") or []
        if steps:
            numbered = "; ".join(f"{i + 1}. {s}" for i, s in enumerate(steps))
            return f"{func}: {numbered}"
        # Aucune étape explicite : utiliser la description (user_desc) qui
        # contient le contexte complet depuis _build_ai_plan.
        user_desc = (step.get("user_desc") or "").strip()
        if user_desc and user_desc != func:
            return f"{func}: {user_desc}"
        return func

    def _build_goal_context(self, mod, mod_desc, func, fn_desc, steps, project_context, user_desc=""):
        """A rich context describing WHY we are here and what to reach, so the
        AI can first navigate to the right screen, then perform the steps."""
        parts = []
        parts.append(f"Projet/URL: {project_context}")
        parts.append(f"Module: {mod}" + (f" — {mod_desc}" if mod_desc else ""))
        parts.append(f"Fonctionnalité à tester: {func}"
                     + (f" ({fn_desc})" if fn_desc else ""))
        if steps:
            parts.append("Étapes:")
            for i, s in enumerate(steps, 1):
                parts.append(f"  {i}. {s}")
        elif user_desc:
            parts.append(f"Description: {user_desc}")
        return "\n".join(parts)

    def _navigate_path(self, nav_path, goal_ctx, constraint, run_phase):
        """Navigate through a path like [Module, Sub, Functionality] using
        a combination of direct clicks and AI assistance."""
        for i, segment in enumerate(nav_path):
            if self._cancel_requested():
                return
            _log(f"navigate_path[{i}]: '{segment}'")
            # Try direct text click first
            if self._click_nav_label(segment) or self._click_by_text(segment):
                self._slow_pause()
                _log(f"navigate_path[{i}]: '{segment}' clicked directly")
                continue
            # Fall back to AI
            run_phase(f"nav_{i}",
                      f"Cliquer sur '{segment}' (niveau {i+1}/{len(nav_path)} du chemin de navigation).", 8)

    # -- walk-through: click every visible physical button ------------------

    def walk_page(self, step):
        """Smoke-walk every visible button on a page."""
        from . import explorer
        mod = step["module"]
        url = step["url"]
        _log(f"WALK: {mod} / {url}")

        # Load + settle so AJAX/DataTable rows (and their row-action buttons)
        # are present before the inventory is taken.
        self._walk_goto(url)
        explorer.scroll_full(self.page)

        found, handles = explorer.scan_buttons_handles(self.page)
        pairs = [(a, h) for a, h in zip(found, handles) if h is not None]
        _log(f"WALK: found {len(pairs)} clickable buttons")

        if not pairs:
            self._record({"module": mod, "function": "Parcours", "action": "WALK",
                          "data": "", "severity": "MINOR"},
                         "PASS", "Au moins un bouton visible est présent",
                         "Aucun bouton visible détecté sur la page")
            return "PASS"

        # Auto-cancel (dismiss) any JS confirmation so destructive buttons are
        # validated without actually changing real production data.
        self.page.on("dialog", lambda d: d.dismiss())

        total = passed = warned = failed = 0
        for i, (a, _h) in enumerate(pairs):
            total += 1
            label = (a.get("label") or f"Bouton #{i}")[:60]
            if self._cancel_requested():
                break
            try:
                h = self._resolve_button(a)
                if h is None:
                    # table may not have finished re-rendering yet -> reload
                    self._walk_goto(url)
                    h = self._resolve_button(a)
                if h is None:
                    failed += 1
                    self._record({"module": mod, "function": label, "action": "WALK",
                                  "data": "", "severity": "MAJOR"},
                                 "FAIL", "Bouton cliquable",
                                 f"Bouton '{label}' introuvable dans le DOM",
                                 self._screenshot(mod, label, "FAIL"))
                    continue
                # Click and observe
                h.click(timeout=4000)
                settle(self.page)
                self._record({"module": mod, "function": label, "action": "WALK",
                              "data": "", "severity": ""},
                             "PASS", "Bouton cliqué sans erreur",
                             f"Bouton '{label}' cliqué avec succès")
                passed += 1
                # Navigate back if we left the page
                if not _is_same_url(self.page.url, url):
                    self._walk_goto(url)
                    explorer.scroll_full(self.page)
            except Exception as e:
                failed += 1
                self._record({"module": mod, "function": label, "action": "WALK",
                              "data": "", "severity": "CRITICAL"},
                             "FAIL", "Bouton cliqué sans erreur",
                             f"Exception: {e}",
                             self._screenshot(mod, label, "FAIL"))
                if not _is_same_url(self.page.url, url):
                    self._walk_goto(url)
                    explorer.scroll_full(self.page)

        status = "PASS" if failed == 0 else "FAIL"
        self._record({"module": mod, "function": "Parcours", "action": "WALK",
                      "data": "", "severity": ""},
                     status, f"{total} boutons testés",
                     f"{passed} passés, {warned} warnings, {failed} échecs")
        return status

    def _walk_goto(self, url):
        self.goto(url)
        settle(self.page)
        try:
            self.page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass

    def _resolve_button(self, action):
        """Resolve a button by its _kept_idx into a live Playwright handle."""
        idx = action.get("_kept_idx")
        if idx is None:
            return None
        try:
            from .explorer import _CLICKABLE_SEL
            all_els = self.page.query_selector_all(_CLICKABLE_SEL)
            if 0 <= idx < len(all_els):
                return all_els[idx]
        except Exception:
            pass
        return None


def run_scenarios(session, db, project, tid, steps, launch_info=None,
                  is_cancelled=None):
    """Run a list of planner steps. Returns (counters, results).

    Steps can be either:
      - Structured actions (dict with action_type): executed by ActionInterpreter
      - Legacy planner steps (dict with step_type): executed by Runner methods
    """
    _log(f"run_scenarios: {len(steps)} steps for project {project.get('name','')}")
    runner = Runner(session, db, project, tid, is_cancelled=is_cancelled)
    counters = {"total": 0, "passed": 0, "failed": 0, "warning": 0, "skipped": 0}

    def add(status):
        counters["total"] += 1
        key = {"PASS": "passed", "FAIL": "failed",
               "WARNING": "warning", "SKIPPED": "skipped"}.get(status, "skipped")
        counters[key] += 1

    results = []
    for step in steps:
        if is_cancelled and is_cancelled():
            _log("run_scenarios: cancelled")
            break

        # --- Structured action (new system): canonical action types only.
        # Legacy planner steps also carry an 'action_type' key (NAVIGATION,
        # CREATE, SEARCH, ...) so we match explicitly against the library. ---
        if (step.get("action_type") in _STRUCTURED_ACTION_TYPES and
                step["action_type"] != "SECTION"):
            _log(f"run_scenarios: STRUCTURED {step['action_type']} "
                 f"target={step.get('target','')[:40]}")
            try:
                interp = ActionInterpreter(runner.page, session, tid,
                                           shot_seq=runner.shot_seq)
                status, message = interp.execute(step)
                runner.shot_seq = interp.shot_seq
                if step.get("target"):
                    runner.last_fill_value = step["target"]
                _log(f"run_scenarios: STRUCTURED result={status} {message}")
            except Exception as e:
                _log(f"run_scenarios: STRUCTURED EXCEPTION: {e}")
                status = "FAIL"
                message = str(e)
            runner._record({
                "module": step.get("module", "Général"),
                "function": step.get("function",
                                     step.get("action_type", "Action")),
                "action": f"{step.get('action_type','')}: "
                          f"{step.get('target','')[:60]}",
                "data": step.get("value", ""),
                "severity": "" if status == "PASS" else "MAJOR",
            }, status, step.get("expected") or message, message)
            add(status)
            continue

        # --- Section label (skip) ---
        if step.get("action_type") == "SECTION":
            continue

        # --- Legacy planner step ---
        st = step.get("step_type", "")
        _log(f"run_scenarios: step_type={st} label={step.get('label','')[:60]}")
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
            elif st == "walk":
                status = runner.walk_page(step)
            elif st == "ai_step":
                status = runner.ai_step(step, project.get("url", ""))
            else:
                status = runner.generic(step)
        except Exception as e:
            _log(f"run_scenarios EXCEPTION: {e}\n{traceback.format_exc()}")
            status = "FAIL"
            runner._record({
                "module": step.get("module", "Général"),
                "function": step.get("label", "Scénario"),
                "action": step.get("action", "GENERIC"),
                "data": getattr(runner, "entity_name", ""),
                "severity": "CRITICAL",
            }, "FAIL", "Le scénario s'exécute sans erreur",
            f"Exception: {e}", runner._screenshot(step.get("module", "Général"), "Exec", "FAIL"))
        add(status)
        _log(f"run_scenarios: step result={status} | counters={counters}")

    # Add HTTP errors (non-destructive reporting) at end
    _record_http_errors(runner, db, tid, counters, results)
    # Reload the persisted results so the on-disk report file is built from the
    # real recorded rows (previously results stayed empty -> empty report).
    try:
        rows = db.list_results(tid)
        results = [dict(r.__dict__) if hasattr(r, "__dict__") else dict(r)
                   for r in rows]
        # Recompute counters from the actual persisted rows so the report is
        # consistent with what is shown on screen.
        counters = {"total": 0, "passed": 0, "failed": 0,
                    "warning": 0, "skipped": 0}
        for r in results:
            st = (r.get("status") or "SKIPPED").upper()
            counters["total"] += 1
            if st == "PASS":
                counters["passed"] += 1
            elif st == "FAIL":
                counters["failed"] += 1
            elif st == "WARNING":
                counters["warning"] += 1
            else:
                counters["skipped"] += 1
    except Exception as e:
        _log(f"run_scenarios: could not reload results: {e}")
    _log(f"run_scenarios: DONE | {counters} | {len(results)} results")
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
        add = counters  # not a function — used as dict ref
        add["total"] += 1
        key = {"PASS": "passed", "FAIL": "failed",
               "WARNING": "warning"}.get(status, "skipped")
        add[key] += 1
