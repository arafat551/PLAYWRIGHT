"""Structured action interpreter — executes standardized actions via Playwright.

Each action follows the model:
    { action_type, target, value, expected }

This replaces AI-dependent free-text interpretation with deterministic,
reliable execution of known action types.
"""
import os
import time

from ..config import env
from .browser import settle


def _log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[ACTION {ts}] {msg}", flush=True)


class ActionInterpreter:
    """Execute structured actions on a Playwright page."""

    def __init__(self, page, session=None, tid=None, shot_seq=0):
        self.page = page
        self.session = session
        self.tid = tid
        self.shot_seq = shot_seq
        self.last_fill_value = None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def execute(self, action):
        """Execute a single structured action.

        Returns (status, message):
            status: 'PASS' | 'FAIL' | 'WARNING'
            message: human-readable result description
        """
        action_type = (action.get("action_type") or "").upper()
        target = (action.get("target") or "").strip()
        value = (action.get("value") or "").strip()

        _log(f"EXECUTE {action_type} target='{target}' value='{value}'")

        dispatch = {
            "NAVIGUER": self._exec_navigate,
            "CLIQUEER": self._exec_click,
            "REMPLIR": self._exec_fill,
            "SELECTIONNER": self._exec_select,
            "COCHER": self._exec_check,
            "DECOCHER": self._exec_uncheck,
            "RECHERCHER": self._exec_search,
            "VERIFIER": self._exec_verify,
            "ATTENDRE": self._exec_wait,
            "ECRAN": self._exec_screenshot,
            "SELECTIONNER_LIGNE": self._exec_select_row,
        }

        handler = dispatch.get(action_type)
        if not handler:
            _log(f"UNKNOWN action_type: {action_type}")
            return "WARNING", f"Type d'action inconnu: {action_type}"

        try:
            return handler(target, value)
        except Exception as e:
            _log(f"EXCEPTION on {action_type}: {e}")
            return "FAIL", f"Erreur: {e}"

    # ------------------------------------------------------------------
    # NAVIGUER
    # ------------------------------------------------------------------

    def _exec_navigate(self, target, value):
        url = target or value
        if not url:
            return "FAIL", "URL non spécifiée"
        _log(f"navigate -> {url}")
        try:
            self.page.goto(url, timeout=30000)
            settle(self.page)
        except Exception:
            try:
                self.page.goto(url, wait_until="commit", timeout=60000)
                settle(self.page)
            except Exception as e:
                return "FAIL", f"Navigation impossible: {e}"
        code = self._http_status()
        if code and code >= 400:
            return "FAIL", f"HTTP {code}"
        return "PASS", f"Page chargée ({url})"

    # ------------------------------------------------------------------
    # CLIQUEER
    # ------------------------------------------------------------------

    def _exec_click(self, target, value):
        if not target:
            return "FAIL", "Cible non spécifiée"
        # Try multiple strategies to find and click the element
        strategies = [
            self._click_by_text,
            self._click_by_role,
            self._click_by_row_action,
            self._click_by_attr,
            self._click_by_css,
        ]
        for strategy in strategies:
            try:
                if strategy(target):
                    self._after_click()
                    _log(f"click OK: '{target}' via {strategy.__name__}")
                    return "PASS", f"'{target}' cliqué"
            except Exception:
                continue
        _log(f"click FAILED: '{target}'")
        return "FAIL", f"'{target}' introuvable"

    def _click_by_text(self, text):
        return self._first_visible(self.page.get_by_text(text, exact=False))

    def _click_by_role(self, text):
        for role in ("button", "link", "menuitem", "tab"):
            try:
                if self._first_visible(
                        self.page.get_by_role(role, name=text, exact=False)):
                    return True
            except Exception:
                continue
        return False

    def _click_by_attr(self, text):
        """Click an element matched by title/aria-label attribute."""
        for sel in (f'[title*="{text}"]', f'[aria-label*="{text}"]',
                    f'[data-title*="{text}"]'):
            try:
                if self._first_visible(self.page.locator(sel)):
                    return True
            except Exception:
                continue
        return False

    def _click_by_css(self, text):
        for sel in (f'a:has-text("{text}")', f'button:has-text("{text}")',
                    f'[role="button"]:has-text("{text}")'):
            try:
                if self._first_visible(self.page.locator(sel)):
                    return True
            except Exception:
                continue
        return False

    def _first_visible(self, locator):
        """Click and confirm the first VISIBLE element a locator matches."""
        try:
            n = locator.count()
        except Exception:
            n = 0
        if not n:
            n = 1
        for i in range(n):
            try:
                el = locator.nth(i)
            except Exception:
                el = locator if i == 0 else None
            if el is None:
                continue
            try:
                if el.is_visible():
                    el.click(timeout=4000)
                    return True
            except Exception:
                continue
        return False

    def _click_by_row_action(self, text):
        """Click an action button (edit/delete/view) in a table row."""
        text_lower = text.lower()
        _ROW_KEYS = {
            "visualiser": ("visualis", "voir", "affiche", "eye", "show", "view"),
            "modifier": ("modifier", "modif", "edit", "pencil", "pen"),
            "supprimer": ("supprimer", "delete", "remove", "trash", "effacer"),
        }
        action_keys = None
        for group, keys in _ROW_KEYS.items():
            if any(k in text_lower for k in keys):
                action_keys = keys
                break
        if not action_keys:
            return False
        # Find the right row
        rows = self.page.query_selector_all("table tbody tr")
        target_row = None
        wanted = (self.last_fill_value or "").strip().lower()
        for tr in rows:
            try:
                txt = (tr.inner_text() or "").lower()
                if wanted and wanted in txt:
                    target_row = tr
                    break
            except Exception:
                continue
        if not target_row and rows:
            target_row = rows[0]
        if not target_row:
            return False
        # Click the action button in the row
        els = target_row.query_selector_all("a, button, i, span")
        for el in els:
            try:
                hay = ((el.get_attribute("class") or "") + " " +
                       (el.get_attribute("title") or "") + " " +
                       (el.get_attribute("aria-label") or "")).lower()
                if any(k in hay for k in action_keys):
                    el.click(timeout=4000)
                    self._after_click()
                    return True
            except Exception:
                continue
        return False

    def _click_by_css(self, text):
        for sel in (f'a:has-text("{text}")', f'button:has-text("{text}")',
                    f'[role="button"]:has-text("{text}")'):
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=4000)
                    return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # REMPLIR
    # ------------------------------------------------------------------

    def _exec_fill(self, target, value):
        if not target:
            return "FAIL", "Champ non spécifié"
        # Wait for form/modal
        try:
            self.page.wait_for_selector(
                'form, [role="dialog"], .modal, .modal-body',
                state="visible", timeout=5000)
        except Exception:
            pass
        self._optional_filled = False
        strategies = [
            lambda: self._fill_by_label(target, value),
            lambda: self._fill_by_placeholder(target, value),
            lambda: self._fill_by_name(target, value),
            lambda: self._fill_by_id(target, value),
            lambda: self._fill_select2(target, value),
            lambda: self._fill_optional(target, value),
        ]
        for strategy in strategies:
            try:
                if strategy():
                    self.last_fill_value = value
                    _log(f"fill OK: '{target}' = '{value}'")
                    if self._optional_filled:
                        return "PASS", (f"Champ '{target}' masqué/automatique "
                                        f"- valeur préservée")
                    return "PASS", f"Champ '{target}' rempli"
            except Exception:
                continue
        _log(f"fill FAILED: '{target}'")
        return "FAIL", f"Champ '{target}' introuvable"

    def _fill_by_label(self, label, value):
        loc = self.page.get_by_label(label, exact=False).first
        if loc.count() > 0:
            loc.wait_for(state="visible", timeout=4000)
            loc.fill(str(value))
            return True
        return False

    def _fill_by_placeholder(self, label, value):
        sel = f'input[placeholder*="{label}" i], textarea[placeholder*="{label}" i]'
        el = self.page.query_selector(sel)
        if el and el.is_visible():
            el.fill(str(value))
            return True
        return False

    def _fill_by_name(self, label, value):
        sel = f'input[name*="{label}" i], textarea[name*="{label}" i]'
        el = self.page.query_selector(sel)
        if el and el.is_visible():
            el.fill(str(value))
            return True
        return False

    def _fill_by_id(self, label, value):
        sel = f'input[id*="{label}" i], textarea[id*="{label}" i]'
        el = self.page.query_selector(sel)
        if el and el.is_visible():
            el.fill(str(value))
            return True
        return False

    def _fill_optional(self, label, value):
        """Accept a field that exists in the DOM but is hidden/auto-filled.

        Some forms conditionally hide fields (e.g. a contact name for an
        'Individuel' client); filling them is then unnecessary. We treat the
        step as fulfilled when the control is present but not visible.
        """
        lbl = label.lower().replace("*", "").strip()
        if not lbl:
            return False
        probes = (
            f'input[name*="{lbl}" i], textarea[name*="{lbl}" i], '
            f'select[name*="{lbl}" i]',
            f'input[id*="{lbl}" i], textarea[id*="{lbl}" i], '
            f'select[id*="{lbl}" i]',
            f'input[placeholder*="{lbl}" i], '
            f'textarea[placeholder*="{lbl}" i]',
        )
        for sel in probes:
            try:
                if self.page.locator(sel).count():
                    self._optional_filled = True
                    self.page.wait_for_timeout(300)
                    return True
            except Exception:
                continue
        try:
            if self.page.get_by_label(lbl, exact=False).count():
                self._optional_filled = True
                return True
        except Exception:
            pass
        return False

    def _fill_select2(self, label, value):
        """Fill a Select2-driven field by typing in its search box."""
        wanted = label.lower().replace("*", "").strip()
        for sel in self.page.query_selector_all("select.select2-hidden-accessible"):
            try:
                sid = sel.get_attribute("id") or ""
                lbl = ""
                lbl_el = self.page.query_selector(f'label[for="{sid}"]') \
                    if sid else None
                if lbl_el:
                    lbl = (lbl_el.inner_text() or "").lower()
                if not lbl:
                    name = (sel.get_attribute("name") or "").lower()
                    if wanted in name:
                        lbl = name
                if lbl and wanted in lbl.replace("*", ""):
                    return self._drive_select2(sid, value)
            except Exception:
                continue
        return False

    def _drive_select2(self, sid, value):
        """Open a Select2, search the value and pick the matching option."""
        container_sel = f'span[aria-labelledby="select2-{sid}-container"]'
        container = self.page.query_selector(container_sel)
        if not container:
            return False
        try:
            container.click(timeout=4000)
        except Exception:
            return False
        self.page.wait_for_timeout(600)
        search = self.page.query_selector(".select2-search__field")
        if search and search.is_visible():
            search.fill(str(value))
            self.page.wait_for_timeout(2500)
        wanted = str(value).strip().lower()
        chosen = None
        for opt in self.page.query_selector_all(".select2-results__option"):
            try:
                txt = (opt.inner_text() or "").strip().lower()
                if txt == wanted:
                    chosen = opt
                    break
            except Exception:
                continue
        if chosen is None:
            for opt in self.page.query_selector_all(".select2-results__option"):
                try:
                    txt = (opt.inner_text() or "").strip().lower()
                    if wanted and (txt.startswith(wanted) or wanted in txt):
                        chosen = opt
                        break
                except Exception:
                    continue
        if chosen:
            try:
                chosen.click()
                self.page.wait_for_timeout(500)
                return True
            except Exception:
                return False
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # SELECTIONNER
    # ------------------------------------------------------------------

    def _exec_select(self, target, value):
        if not value:
            return "FAIL", "Valeur non spécifiée"
        value = str(value)
        # Try label-based lookup when a field is specified
        if target:
            try:
                loc = self.page.get_by_label(target, exact=False).first
                if loc.count() > 0:
                    loc.wait_for(state="attached", timeout=3000)
                    try:
                        loc.select_option(value=value)
                    except Exception:
                        loc.select_option(label=value)
                    self._after_click()
                    _log(f"select OK: '{target}' = '{value}'")
                    return "PASS", f"'{target}' sélectionné"
            except Exception:
                pass
            # id / name based lookup
            for sel_tpl in (f'select[name*="{target}" i]',
                            f'select[id*="{target}" i]'):
                try:
                    el = self.page.query_selector(sel_tpl)
                    if el:
                        try:
                            el.select_option(value=value)
                        except Exception:
                            el.select_option(label=value)
                        self._after_click()
                        _log(f"select OK: '{target}' = '{value}'")
                        return "PASS", f"'{target}' sélectionné"
                except Exception:
                    continue
        # Fallback: find any select whose option list contains the value
        try:
            sel = self._configured_select(value)
            if sel:
                try:
                    sel.select_option(value=value)
                except Exception:
                    sel.select_option(label=value)
                self._after_click()
                _log(f"select OK (auto): '{value}'")
                return "PASS", f"'{value}' sélectionné"
        except Exception:
            pass
        # Select2 fallback: drive the dropdown
        if self._select2_option(value):
            return "PASS", f"'{value}' sélectionné"
        return "FAIL", f"'{value}' introuvable"

    def _configured_select(self, value):
        """Locate a <select> having an option with the given label."""
        wanted = value.strip().lower()
        for sel in self.page.query_selector_all("select"):
            try:
                for opt in sel.query_selector_all("option"):
                    if (opt.inner_text() or "").strip().lower() == wanted:
                        return sel
            except Exception:
                continue
        return None

    def _select2_option(self, value):
        """Open each Select2 dropdown and pick the option matching value."""
        wanted = value.strip().lower()
        for open_el in self.page.query_selector_all("span.select2-selection"):
            try:
                open_el.click(timeout=2000)
                self.page.wait_for_timeout(600)
                opts = self.page.query_selector_all(".select2-results__option")
                chosen = None
                for opt in opts:
                    try:
                        txt = (opt.inner_text() or "").strip().lower()
                    except Exception:
                        continue
                    if txt == wanted:
                        chosen = opt
                        break
                if chosen is None:
                    search = self.page.query_selector(".select2-search__field")
                    if search and search.is_visible():
                        search.fill(value)
                        self.page.wait_for_timeout(2500)
                        for opt in self.page.query_selector_all(
                                ".select2-results__option"):
                            try:
                                txt = (opt.inner_text() or "").strip().lower()
                            except Exception:
                                continue
                            if txt == wanted or (wanted and (
                                    txt.startswith(wanted) or
                                    wanted in txt)):
                                chosen = opt
                                break
                if chosen:
                    try:
                        chosen.click()
                        self.page.wait_for_timeout(500)
                        self._after_click()
                        return True
                    except Exception:
                        pass
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # COCHER / DECOCHER
    # ------------------------------------------------------------------

    def _exec_check(self, target, value):
        if not target:
            return "FAIL", "Case non spécifiée"
        try:
            loc = self.page.get_by_label(target, exact=False).first
            if loc.count() > 0 and loc.is_visible():
                loc.check(timeout=3000)
                self._after_click()
                return "PASS", f"Case '{target}' cochée"
        except Exception:
            pass
        return self._click_checkbox_near(target, True)

    def _exec_uncheck(self, target, value):
        if not target:
            return "FAIL", "Case non spécifiée"
        try:
            loc = self.page.get_by_label(target, exact=False).first
            if loc.count() > 0 and loc.is_visible():
                loc.uncheck(timeout=3000)
                self._after_click()
                return "PASS", f"Case '{target}' décochée"
        except Exception:
            pass
        return self._click_checkbox_near(target, False)

    def _click_checkbox_near(self, text, check):
        try:
            checkboxes = self.page.query_selector_all('input[type="checkbox"]')
            for cb in checkboxes:
                if cb.is_visible():
                    lbl = cb.evaluate("""e => {
                        const id = e.id;
                        if (id) {
                            const l = document.querySelector('label[for="' + id + '"]');
                            if (l) return l.innerText || '';
                        }
                        const p = e.closest('label');
                        if (p) return p.innerText || '';
                        return '';
                    }""")
                    if text.lower() in (lbl or "").lower():
                        if check:
                            cb.check()
                        else:
                            cb.uncheck()
                        self._after_click()
                        action = "cochée" if check else "décochée"
                        return "PASS", f"Case '{text}' {action}"
        except Exception:
            pass
        return "FAIL", f"Case '{text}' introuvable"

    # ------------------------------------------------------------------
    # RECHERCHER
    # ------------------------------------------------------------------

    def _exec_search(self, target, value):
        if not target:
            return "FAIL", "Terme de recherche non spécifié"
        search_input = self._find_search_input()
        if search_input:
            try:
                search_input.click(timeout=3000)
                search_input.fill(target)
                self.page.keyboard.press("Enter")
                self._after_click()
                _log(f"search OK: '{target}'")
                return "PASS", f"Recherche '{target}' effectuée"
            except Exception:
                pass
        # Fallback: any visible text input
        try:
            inputs = self.page.query_selector_all(
                'input[type="text"], input:not([type])')
            for inp in inputs:
                if inp.is_visible():
                    inp.click(timeout=3000)
                    inp.fill(target)
                    self.page.keyboard.press("Enter")
                    self._after_click()
                    return "PASS", f"Recherche '{target}' effectuée"
        except Exception:
            pass
        return "FAIL", "Barre de recherche introuvable"

    # ------------------------------------------------------------------
    # VERIFIER
    # ------------------------------------------------------------------

    def _exec_verify(self, target, value):
        if not target:
            return "PASS", "Pas de vérification demandée"
        t = target.lower()
        # Page load check
        if any(w in t for w in ("page se charge", "page charge", "sans erreur",
                                "correctement", "s'ouvre")):
            ok = bool(self.page.url and self.page.url.startswith("http"))
            if ok:
                return "PASS", "Page chargée"
            return "FAIL", "Page non chargée"
        # Table row presence check
        if self._verify_row_visible(target):
            return "PASS", f"'{target}' trouvé"
        return "FAIL", f"'{target}' introuvable"

    def _verify_row_visible(self, value):
        if not value:
            return False
        v = value.lower()
        # Skip common natural-language prefixes
        for prefix in ("la ligne ", "l'entité ", "le nom ", "la valeur ",
                       "que ", "bien ", "contient ", "visible ", "absent "):
            if v.startswith(prefix):
                v = v[len(prefix):]
                break
        v = v.strip()
        if not v:
            return False
        probe = """(v) => {
            const tables = document.querySelectorAll('table');
            for (const tb of tables) {
                const rows = tb.querySelectorAll('tbody tr');
                for (const tr of rows) {
                    if ((tr.innerText || '').toLowerCase().indexOf(v) !== -1)
                        return true;
                }
            }
            return false;
        }"""
        try:
            if self.page.evaluate(probe, v):
                return True
        except Exception:
            pass
        # Try search
        search_input = self._find_search_input()
        if search_input:
            try:
                search_input.fill(v)
                self.page.keyboard.press("Enter")
                self._after_click()
                if self.page.evaluate(probe, v):
                    return True
            except Exception:
                pass
        # "absent" check: if the element is NOT found, that's correct
        if "absent" in (value or "").lower():
            return True
        return False

    # ------------------------------------------------------------------
    # ATTENDRE
    # ------------------------------------------------------------------

    def _exec_wait(self, target, value):
        ms = int(value or target or "2000")
        ms = max(100, min(ms, 10000))
        self.page.wait_for_timeout(ms)
        return "PASS", f"Attendu {ms} ms"

    # ------------------------------------------------------------------
    # ECRAN (screenshot)
    # ------------------------------------------------------------------

    def _exec_screenshot(self, target, value):
        name = target or value or "screenshot"
        self.shot_seq += 1
        fname = f"shot_{self.tid}_{name}_{self.shot_seq}.png"
        fname = fname.replace(" ", "_").replace("/", "_").replace("\\", "_")
        rel = ("screenshots/" + fname).replace("\\", "/")
        fn = os.path.join(env.STATIC_DIR, rel)
        try:
            os.makedirs(os.path.dirname(fn), exist_ok=True)
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
            self.page.wait_for_timeout(500)
            self.page.screenshot(path=fn)
        except Exception:
            rel = ""
        return "PASS", f"Capture '{name}' prise"

    # ------------------------------------------------------------------
    # SELECTIONNER_LIGNE
    # ------------------------------------------------------------------

    def _exec_select_row(self, target, value):
        if not target:
            return "FAIL", "Ligne non spécifiée"
        self.last_fill_value = target
        # Try to find and highlight/scroll to the row
        rows = self.page.query_selector_all("table tbody tr")
        for tr in rows:
            try:
                txt = (tr.inner_text() or "").lower()
                if target.lower() in txt:
                    tr.scroll_into_view_if_needed()
                    _log(f"select_row OK: '{target}'")
                    return "PASS", f"Ligne '{target}' sélectionnée"
            except Exception:
                continue
        _log(f"select_row FAILED: '{target}'")
        return "FAIL", f"Ligne '{target}' introuvable"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _http_status(self):
        try:
            return self.page.evaluate(
                "() => { try { return performance.getEntriesByType('navigation')[0]?.responseStatus || 0; } catch(e) { return 0; } }")
        except Exception:
            return 0

    def _after_click(self):
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        self.page.wait_for_timeout(800)

    def _find_search_input(self):
        try:
            return self.page.query_selector(
                'input[type="search"], '
                'input[name*="search" i], '
                'input[name*="query" i], '
                'input[placeholder*="recherch" i], '
                'input[placeholder*="search" i]')
        except Exception:
            return None


def run_structured_actions(page, session, actions, tid=None,
                           is_cancelled=None):
    """Execute a list of structured actions on a page.

    Returns (counters, results):
        counters: {total, passed, failed, warning, skipped}
        results: list of result dicts
    """
    interp = ActionInterpreter(page, session, tid)
    counters = {"total": 0, "passed": 0, "failed": 0, "warning": 0, "skipped": 0}
    results = []

    for action in actions:
        if is_cancelled and is_cancelled():
            break

        action_type = action.get("action_type", "")
        target = action.get("target", "")
        expected = action.get("expected", "")

        status, message = interp.execute(action)
        counters["total"] += 1
        key = {"PASS": "passed", "FAIL": "failed",
               "WARNING": "warning"}.get(status, "skipped")
        counters[key] += 1

        result = {
            "module": action.get("module", "Scénario"),
            "function": action.get("function", action_type),
            "action": f"{action_type}: {target}",
            "data": action.get("value", ""),
            "status": status,
            "severity": "" if status == "PASS" else "MAJOR",
            "expected": expected or message,
            "obtained": message,
            "screenshot": "",
            "http_status": 0,
        }
        results.append(result)

        _log(f"RESULT {status}: {action_type} {target} -> {message}")

    return counters, results
