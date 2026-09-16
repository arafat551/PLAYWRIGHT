"""Option C: generate a runnable scenario from a live screen.

Opens the real screen of a functionality, detects its creation form and
writes human steps that the fallback engine can execute:

    Cliquer sur le bouton Ajouter
    Remplir le champ Nom avec QA_CATEGORIE_150937
    Cliquer sur Sauver
    Vérifier que la ligne Contient QA_CATEGORIE_150937

The generation never saves anything to the target application: it only opens
the form to inspect its fields.
"""
import datetime
import re
import threading
import time

from .. import security
from . import browser
from .browser import has_login_form, try_login
from .forms import _field_type, _label_for_field, _required, _tag, find_form


def _log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[GENERATOR {ts}] {msg}", flush=True)


class ScanCancelled(Exception):
    """Scan stopped by the user."""


class ScanControl:
    """Thread-safe pause/stop control shared between the scan thread and the
    web layer.  The scan calls ``check()`` at module & functionality boundaries.
    """
    def __init__(self):
        self._cancelled = threading.Event()
        self._paused = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def set_paused(self, v):
        if v:
            self._paused.set()
        else:
            self._paused.clear()

    @property
    def paused(self):
        return self._paused.is_set()

    def check(self):
        if self._cancelled.is_set():
            raise ScanCancelled()
        while self._paused.is_set():
            if self._cancelled.is_set():
                raise ScanCancelled()
            time.sleep(0.25)

_CREATE_WORDS = ("ajouter", "ajoutez", "nouveau", "nouvelle", "nouvel", "créer",
                 "creer", "création", "creation", "add", "new", "create")
_CREATE_ICONS = ("fa-plus", "bi-plus", "icon-plus", "add-icon", "plus-circle",
                 "la-plus", "mdi-plus")
_SAVE_WORDS = ("sauver", "enregistrer", "valider", "créer", "creer", "ajouter",
               "save", "submit", "ok", "confirmer")
_NAME_WORDS = ("nom", "name", "intitulé", "intitule", "libellé", "libelle",
               "titre", "title", "label")
_EDIT_WORDS = ("modifier", "edit", "éditer", "editer", "update", "pencil",
               "pen", "fa-edit", "fa-pen", "bi-pencil", "edit-service")
_DELETE_WORDS = ("supprimer", "delete", "remove", "trash", "effacer",
                 "erase", "fa-trash", "bi-trash", "delete-service")
_CONFIRM_WORDS = ("confirmer", "ok", "oui", "yes", "accepter", "valider")


def _norm(s):
    return (s or "").lower().replace("é", "e").replace("è", "e") \
        .replace("à", "a").replace("ô", "o").replace("î", "i") \
        .replace("û", "u").replace("ç", "c").strip()


def _suffix():
    return datetime.datetime.now().strftime("%d%H%M%S")


def _click_text(page, text):
    """Click a navigation/menu element by its text. Reuses Runner strategies.

    Falls back to accent-insensitive and fuzzy matching so a stored path like
    "Catégorie de Service" still finds the real "Catégories de service".
    """
    if not text or not str(text).strip():
        return False
    wanted = str(text).strip()
    for sel in (
        f'nav >> text="{wanted}"',
        f'aside >> text="{wanted}"',
        f'[role="navigation"] >> text="{wanted}"',
        f'.sidebar >> text="{wanted}"',
        f'.menu >> text="{wanted}"',
        f'a:has-text("{wanted}")',
        f'button:has-text("{wanted}")',
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=4000)
                page.wait_for_timeout(800)
                return True
        except Exception:
            continue
    # Fuzzy fallback: scan visible nav/links/buttons and match on the folded
    # (accent-insensitive) text with loose word overlap.
    try:
        hit = page.evaluate(
            """(want) => {
                const fold = s => (s || '').toLowerCase()
                    .replace(/\\u00e9/g,'e').replace(/\\u00e8/g,'e')
                    .replace(/\\u00ea/g,'e').replace(/\\u00e0/g,'a')
                    .replace(/\\u00f4/g,'o').replace(/\\u00ee/g,'i')
                    .replace(/\\u00fb/g,'u').replace(/\\u00e7/g,'c')
                    .trim();
                const tw = fold(want).split(/\\s+/).filter(Boolean);
                const els = Array.from(document.querySelectorAll(
                    'nav a, nav button, .sidebar a, .menu a, aside a, aside button, a, button'));
                for (const el of els) {
                    const t = fold(el.innerText || el.value || '');
                    if (!t) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    const words = t.split(/\\s+/).filter(Boolean);
                    if (tw.every(w => words.some(ww => ww === w || ww.includes(w) || w.includes(ww)))) {
                        el.click(); return true;
                    }
                }
                return false;
            }""", wanted)
        if hit:
            page.wait_for_timeout(800)
            return True
    except Exception:
        pass
    return False


def _scan_menu_items(page):
    """Scan visible navigation/menu items on the page.

    Focuses on nav/aside/sidebar/menu elements first, then expands
    to all visible links if no menu is found.
    Returns a list of dicts: {label, tag, is_visible, rect, in_nav}
    """
    try:
        return page.evaluate(
            """() => {
                // First pass: scan only navigation elements
                const navSel = 'nav a, nav button, aside a, aside button, ' +
                    '[role="navigation"] a, [role="navigation"] button, ' +
                    '.sidebar a, .sidebar button, .menu a, .menu button, ' +
                    '.nav-link, .menu-item, .nav-item a, [role="menuitem"]';
                const els = Array.from(document.querySelectorAll(navSel));
                const out = [];
                const seen = new Set();
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    const cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    const label = (el.innerText || el.value || '').trim();
                    if (!label || label.length > 80 || seen.has(label.toLowerCase())) continue;
                    seen.add(label.toLowerCase());
                    out.push({
                        label: label,
                        tag: el.tagName.toLowerCase(),
                        x: r.x, y: r.y, w: r.width, h: r.height,
                        in_nav: true,
                        href: el.tagName === 'A' ? (el.href || '') : ''
                    });
                }
                // If we found nav items, return them
                if (out.length > 0) return out;
                // Fallback: scan all visible links
                const allSel = 'a[href], button';
                const allEls = Array.from(document.querySelectorAll(allSel));
                for (const el of allEls) {
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    const cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    const label = (el.innerText || el.value || '').trim();
                    if (!label || label.length > 80 || seen.has(label.toLowerCase())) continue;
                    seen.add(label.toLowerCase());
                    out.push({
                        label: label,
                        tag: el.tagName.toLowerCase(),
                        x: r.x, y: r.y, w: r.width, h: r.height,
                        in_nav: false,
                        href: el.tagName === 'A' ? (el.href || '') : ''
                    });
                }
                return out;
            }""")
    except Exception:
        return []


def _sidebar_tree(page):
    """Harvest the full sidebar navigation tree, including collapsed sections.

    Many admin templates (AdminLTE, Metronic, ...) render the complete module
    tree in the DOM with hidden sub-menus (`display:none`). A visibility-only
    scan would miss every collapsed module, so here we read the structure
    directly and extract section + links regardless of visibility.

    Returns a list of sections:
      {"name": str, "href": str, "links": [{"label", "href"}, ...]}
      - a section with links is an expandable module (its own href is empty)
      - a section without links is a plain top-level page
    """
    try:
        return page.evaluate(
            """() => {
                const pick = (anchor) => {
                    const clone = anchor.cloneNode(true);
                    clone.querySelectorAll('i, .badge, .fa, .far, .fas, ' +
                        '.right, .float-right, .submenu-indicator, ' +
                        '[class*="icon"], [class*="fa-"]').forEach(e => e.remove());
                    return (clone.innerText || '').replace(/\\s+/g, ' ').trim();
                };
                const root = document.querySelector('#main-sidebar') ||
                    document.querySelector('aside .sidebar nav') ||
                    document.querySelector('.sidebar nav') ||
                    document.querySelector('aside nav');
                if (!root) return [];
                const tree = root.querySelector('ul.nav-sidebar') ||
                    root.querySelector('ul');
                if (!tree) return [];
                const sections = [];
                for (const li of tree.children) {
                    const a = li.querySelector(':scope > a');
                    if (!a) continue;
                    const href = (a.getAttribute('href') || '').trim();
                    const sub = li.querySelector(':scope > ul');
                    if (sub) {
                        const links = [];
                        sub.querySelectorAll('a[href]').forEach(x => {
                            const h = (x.getAttribute('href') || '').trim();
                            if (!h || h === '#' || h.startsWith('#') ||
                                h.startsWith('javascript:')) return;
                            links.push({
                                label: pick(x) || h.split('/').filter(Boolean).pop() || '',
                                href: x.href
                            });
                        });
                        sections.push({name: pick(a), href: '', links});
                    } else {
                        sections.push({name: pick(a), href: a.href || ''});
                    }
                }
                return sections;
            }""")
    except Exception:
        return []


def _fuzzy_match(text, target):
    """Check if text matches target using accent-insensitive fuzzy matching.
    Returns a score (higher = better match). 0 means no match.
    """
    def fold(s):
        return (s or '').lower().replace('é', 'e').replace('è', 'e') \
            .replace('ê', 'e').replace('à', 'a').replace('ô', 'o') \
            .replace('î', 'i').replace('û', 'u').replace('ç', 'c').strip()
    t = fold(text)
    tgt = fold(target)
    if not t or not tgt:
        return 0
    # Exact match
    if t == tgt:
        return 100
    # Target contained in text
    if tgt in t:
        return 80
    # Text contained in target
    if t in tgt:
        return 60
    # Word overlap
    t_words = set(t.split())
    tgt_words = set(tgt.split())
    common = t_words & tgt_words
    if common:
        return 40 + len(common) * 10
    # Partial word match
    for tw in t_words:
        for gw in tgt_words:
            if tw in gw or gw in tw:
                return 30
    return 0


def _expand_accordion_menus(page):
    """Click on accordion/collapsible menu toggles to expand hidden items.

    Handles both generic toggles ([data-toggle], .dropdown-toggle, collapsed
    links) and treeview menus (AdminLTE-style ``.has-treeview`` / submenu
    openers whose children are hidden with ``display:none``).
    Nested sections are expanded recursively.

    Returns True if any menu was expanded.
    """
    expanded = False
    try:
        toggles = page.query_selector_all(
            '[data-toggle], [data-bs-toggle], .accordion-toggle, '
            '.nav-link.collapsed, .menu-toggle, .sidebar-toggle, '
            '.dropdown-toggle, [aria-expanded="false"]')
        for t in toggles:
            try:
                if t.is_visible():
                    t.click(timeout=2000)
                    page.wait_for_timeout(500)
                    expanded = True
            except Exception:
                continue
    except Exception:
        pass
    # Treeviews: click every opener whose submenu is still hidden.
    try:
        for depth in range(3):
            page.evaluate(
                """() => {
                    const list = document.querySelectorAll(
                        'li.nav-item[a href], li[class*="treeview"] > a, ' +
                        '#main-sidebar li.nav-item > a.nav-link');
                    let clicked = false;
                    for (const a of list) {
                        const li = a.parentElement;
                        const sub = li.querySelector(':scope > ul');
                        if (!sub || !li.classList.contains('menu-open')) {
                            if (a.getAttribute('aria-expanded') !== 'true') {
                                a.click();
                                clicked = true;
                            }
                            if (li.classList.contains('menu-open')) {}
                        }
                    }
                }""")
            page.wait_for_timeout(600)
    except Exception:
        pass
    return expanded


def _find_best_match(items, target, threshold=40):
    """Find the best matching item for a target string.

    Returns (item, score) or (None, 0).
    """
    best_score = 0
    best_item = None
    for item in items:
        score = _fuzzy_match(item["label"], target)
        if score > best_score:
            best_score = score
            best_item = item
    if best_score >= threshold:
        return best_item, best_score
    return None, 0


def _auto_discover_and_navigate(page, module, functionality):
    """Scan the menu/sidebar to find and navigate to the functionality.

    Strategy:
      1. Expand accordion menus if any
      2. Try to find the functionality directly in visible menu items
      3. Try to find the module, click it, then find the functionality
      4. Retry with expanded menus
    Returns True if navigation succeeded.
    """
    _log("_auto_discover: module='%s' functionality='%s'" % (module, functionality))

    # Step 0: expand any accordion/collapsible menus
    _expand_accordion_menus(page)
    page.wait_for_timeout(500)

    items = _scan_menu_items(page)
    _log("_auto_discover: found %d menu items" % len(items))
    if items:
        for it in items[:10]:
            _log("  item: '%s' (score=%d)" % (it['label'], _fuzzy_match(it['label'], functionality)))

    if not items:
        return False

    # 1) Try direct match on functionality name
    match, score = _find_best_match(items, functionality)
    if match:
        lbl = match["label"]
        _log("_auto_discover: direct match '%s' (score=%d)" % (lbl, score))
        try:
            loc = page.get_by_text(lbl, exact=False).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=4000)
                page.wait_for_timeout(1000)
                return True
        except Exception:
            pass

    # 2) Try module first, then functionality
    if module and module.lower() not in ("général", "general"):
        mod_match, mod_score = _find_best_match(items, module)
        if mod_match:
            mod_lbl = mod_match["label"]
            _log("_auto_discover: clicking module '%s' (score=%d)" % (mod_lbl, mod_score))
            try:
                loc = page.get_by_text(mod_lbl, exact=False).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=4000)
                    page.wait_for_timeout(1500)
                    # Scan again for the functionality in the sub-menu
                    _expand_accordion_menus(page)
                    page.wait_for_timeout(500)
                    sub_items = _scan_menu_items(page)
                    _log("_auto_discover: after module click, %d sub-items" % len(sub_items))
                    sub_match, sub_score = _find_best_match(sub_items, functionality)
                    if sub_match:
                        sub_lbl = sub_match["label"]
                        _log("_auto_discover: found sub '%s' (score=%d)" % (sub_lbl, sub_score))
                        try:
                            loc2 = page.get_by_text(sub_lbl, exact=False).first
                            if loc2.count() > 0 and loc2.is_visible():
                                loc2.click(timeout=4000)
                                page.wait_for_timeout(1000)
                                return True
                        except Exception:
                            pass
            except Exception:
                pass

    # 3) Retry: expand menus again and rescan
    _expand_accordion_menus(page)
    page.wait_for_timeout(500)
    items2 = _scan_menu_items(page)
    match2, score2 = _find_best_match(items2, functionality)
    if match2 and score2 > score:
        lbl = match2["label"]
        _log("_auto_discover: retry match '%s' (score=%d)" % (lbl, score2))
        try:
            loc = page.get_by_text(lbl, exact=False).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=4000)
                page.wait_for_timeout(1000)
                return True
        except Exception:
            pass

    # 4) Fallback: click the best matching item even with low score
    if match and score >= 25:
        lbl = match["label"]
        _log("_auto_discover: fallback click '%s' (score=%d)" % (lbl, score))
        try:
            loc = page.get_by_text(lbl, exact=False).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=4000)
                page.wait_for_timeout(1000)
                return True
        except Exception:
            pass

    _log("_auto_discover: FAILED - no matching menu item found")
    return False


def open_screen(project, nav_path=None, module="", functionality="", url=""):
    """Open a session and navigate to the functionality's screen.

    If url is provided, navigates directly to it (most reliable).
    Otherwise, if nav_path is provided, follows it step by step.
    Otherwise auto-discovers the path by scanning menus.

    Returns (page, session, stop) once the screen is settled, or
    (None, None, None) if the session can't be used.
    """
    from .browser import open_session
    session, stop = open_session()
    page = session.page
    try:
        try:
            session.goto(project["url"])
        except Exception:
            session.goto(project["url"], wait_until="commit", timeout=60000)
        browser.settle(page)
        if project.get("auth_type", "none") != "none" and has_login_form(page):
            try_login(page, project.get("email", ""),
                      security.decrypt_value(project.get("password_enc", "")))
            browser.settle(page)
        auto_discovered = False
        if url:
            _log(f"open_screen: direct URL '{url}'")
            try:
                page.goto(url, timeout=30000)
                browser.settle(page)
            except Exception as e:
                _log(f"open_screen: direct URL failed ({e})")
        elif nav_path:
            # Explicit path: follow it step by step
            for label in nav_path:
                if _click_text(page, label):
                    continue
            if _click_text(page, module):
                pass
            if _click_text(page, functionality):
                pass
        else:
            # No path: auto-discover by scanning menus
            _log(f"open_screen: no nav_path, auto-discovering '{functionality}'")
            auto_discovered = _auto_discover_and_navigate(page, module, functionality)
            if not auto_discovered:
                _log(f"open_screen: auto-discover FAILED for '{functionality}'")
        page.wait_for_timeout(900)
        return page, session, stop
    except Exception:
        try:
            session.close()
        except Exception:
            pass
        try:
            stop()
        except Exception:
            pass
        raise


def has_create_button(page):
    """True when the screen exposes an 'Ajouter'-like creation button."""
    try:
        return bool(page.evaluate(
            """(words) => {
                const els = Array.from(document.querySelectorAll(
                    'button, a, [role="button"], input[type="button"]'));
                for (const el of els) {
                    const t = ((el.innerText || el.value || '')).trim().toLowerCase();
                    if (t && t.length <= 60 && words.some(w => t.includes(w))) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) return true;
                    }
                }
                return false;
            }""", list(_CREATE_WORDS)))
    except Exception:
        return False


def click_create_button(page):
    """Click the add/create button if present. Returns True when clicked."""
    try:
        ok = bool(page.evaluate(
            """(words) => {
                const els = Array.from(document.querySelectorAll(
                    'button, a, [role="button"], input[type="button"]'));
                for (const el of els) {
                    const t = ((el.innerText || el.value || '')).trim().toLowerCase();
                    if (t && t.length <= 60 && words.some(w => t.includes(w))) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) { el.click(); return true; }
                    }
                }
                return false;
            }""", list(_CREATE_WORDS)))
        if ok:
            page.wait_for_timeout(1000)
            return True
    except Exception:
        pass
    try:
        ok = bool(page.evaluate(
            """(icons) => {
                const els = Array.from(document.querySelectorAll('i, span, svg'));
                for (const el of els) {
                    const btn = el.closest('button, a');
                    if (!btn) continue;
                    const cls = ((el.getAttribute('class') || '') + ' ' +
                                 (btn.getAttribute('class') || '')).toLowerCase();
                    if (icons.some(c => cls.includes(c))) {
                        const r = btn.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) { btn.click(); return true; }
                    }
                }
                return false;
            }""", list(_CREATE_ICONS)))
        if ok:
            page.wait_for_timeout(1000)
            return True
    except Exception:
        pass
    return False


def describe_form(form_el):
    """Structured description of every editable control in the form."""
    fields = []
    try:
        controls = form_el.query_selector_all("input, select, textarea")
    except Exception:
        controls = []
    labels = _collect_labels(form_el)
    for c in controls:
        itype = _field_type(c)
        if itype in ("hidden", "submit", "button", "file", "reset"):
            continue
        label = _visible_label(c, labels)
        if not label:
            label = _label_for_field(c)
        choices = []
        if _tag(c) == "select":
            for o in c.query_selector_all("option"):
                try:
                    v = o.get_attribute("value") or ""
                    t = (o.inner_text() or "").strip()
                    choices.append(t or v)
                except Exception:
                    pass
        fields.append({
            "tag": _tag(c),
            "type": itype,
            "label": label,
            "required": _required(c),
            "choices": [x for x in choices if x],
        })
    return fields


def _collect_labels(form_el):
    """Map label text -> associated control id/name, parsing works without
    depending on ElementHandle.page (not available in this Playwright).
    """
    labels = {}
    try:
        for lb in form_el.query_selector_all("label"):
            txt = ""
            fid = ""
            try:
                txt = (lb.inner_text() or "").strip()
            except Exception:
                pass
            try:
                fid = (lb.get_attribute("for") or "").strip()
            except Exception:
                pass
            if txt:
                labels.setdefault(fid, txt)
    except Exception:
        pass
    return labels


def _visible_label(el, labels):
    try:
        fid = el.get_attribute("id") or ""
        if fid and fid in labels:
            return labels[fid]
    except Exception:
        pass
    try:
        txt = el.evaluate(
            "n => n.closest('label') ? n.closest('label').innerText.trim() : ''")
        if txt:
            return txt
    except Exception:
        pass
    return ""


def submit_label(form_el):
    """Best guess of the form's submit button text."""
    try:
        for sel in ('button[type="submit"]', 'input[type="submit"]',
                    'button[data-submit], button[data-save]'):
            sub = form_el.query_selector(sel)
            if sub:
                txt = ""
                try:
                    txt = (sub.inner_text() or "").strip()
                except Exception:
                    pass
                if not txt:
                    txt = (sub.get_attribute("value") or "").strip()
                if txt:
                    return txt
    except Exception:
        pass
    try:
        big = form_el.evaluate(
            """(words) => {
                const els = Array.from(document.querySelectorAll(
                    'button[type="submit"], input[type="submit"], .modal .btn'));
                for (const el of els) {
                    const t = ((el.innerText || el.value || '')).trim().toLowerCase();
                    if (t && words.some(w => t.includes(w)) && t.length <= 30) {
                        return (el.innerText || el.value || '').trim();
                    }
                }
                return '';
            }""", list(_SAVE_WORDS))
        if big:
            return big
    except Exception:
        pass
    return "Sauver"


def has_table(page):
    try:
        return bool(page.evaluate(
            """() => {
                const tables = Array.from(document.querySelectorAll('table'));
                for (const tb of tables) {
                    const r = tb.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0 &&
                        tb.querySelectorAll('tbody tr, tr').length > 0) return true;
                }
                return false;
            }"""))
    except Exception:
        return False


def _detect_row_actions(page):
    """Detect edit and delete action buttons in table rows.

    Returns (has_edit, has_delete) booleans by scanning the first data row
    for icon/class patterns matching edit or delete actions.
    """
    try:
        result = page.evaluate(
            """(args) => {
                const editWords = args.edit;
                const deleteWords = args.del_;
                const rows = document.querySelectorAll('table tbody tr');
                for (const tr of rows) {
                    const cells = tr.querySelectorAll('td');
                    if (cells.length === 0) continue;
                    // Check the last cell (usually actions column)
                    const lastCell = cells[cells.length - 1];
                    const els = lastCell.querySelectorAll('a, button, i, span, svg');
                    let hasEdit = false, hasDelete = false;
                    for (const el of els) {
                        const hay = ((el.getAttribute('class') || '') + ' ' +
                                     (el.getAttribute('title') || '') + ' ' +
                                     (el.getAttribute('data-icon') || '') + ' ' +
                                     (el.innerText || '')).toLowerCase();
                        if (!hasEdit && editWords.some(w => hay.includes(w))) hasEdit = true;
                        if (!hasDelete && deleteWords.some(w => hay.includes(w))) hasDelete = true;
                    }
                    if (hasEdit || hasDelete) return {hasEdit, hasDelete};
                }
                // Fallback: scan all rows
                for (const tr of rows) {
                    const els = tr.querySelectorAll('a, button, i, span');
                    let hasEdit = false, hasDelete = false;
                    for (const el of els) {
                        const hay = ((el.getAttribute('class') || '') + ' ' +
                                     (el.getAttribute('title') || '') + ' ' +
                                     (el.innerText || '')).toLowerCase();
                        if (!hasEdit && editWords.some(w => hay.includes(w))) hasEdit = true;
                        if (!hasDelete && deleteWords.some(w => hay.includes(w))) hasDelete = true;
                    }
                    if (hasEdit || hasDelete) return {hasEdit, hasDelete};
                }
                return {hasEdit: false, hasDelete: false};
            }""", {"edit": list(_EDIT_WORDS), "del_": list(_DELETE_WORDS)})
        return (result.get("hasEdit", False), result.get("hasDelete", False))
    except Exception:
        return (False, False)


def _detect_confirm_button(page):
    """Detect a confirmation button (for delete dialogs)."""
    try:
        return bool(page.evaluate(
            """(words) => {
                const els = Array.from(document.querySelectorAll(
                    'button, a, [role="button"], input[type="submit"]'));
                for (const el of els) {
                    const t = ((el.innerText || el.value || '')).trim().toLowerCase();
                    if (t && t.length <= 30 && words.some(w => t.includes(w))) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) return true;
                    }
                }
                return false;
            }""", list(_CONFIRM_WORDS)))
    except Exception:
        return False


def _field_value(field, suffix, entity_name):
    """Return (kind, value): kind in {'fill', 'select', 'check'} or None."""
    tag = field.get("tag")
    label = _norm(field.get("label", ""))
    choices = field.get("choices") or []
    if tag == "select":
        for c in choices:
            if c:
                return ("select", c)
        return None
    if field.get("type") in ("checkbox", "radio"):
        return ("check", True)
    if field.get("type") == "email" or ("email" in label or "mail" in label):
        return ("fill", f"qa{suffix}@example.test")
    if field.get("type") in ("tel", "phone") or \
            any(k in label for k in ("tel", "phone", "telephone", "portable")):
        return ("fill", "90123456")
    if field.get("type") == "url":
        return ("fill", "https://example.test")
    if field.get("type") in ("date", "datetime", "datetime-local"):
        return ("fill", "2026-01-15")
    if field.get("type") == "number" or any(k in label for k in
                                            ("quantite", "quantity", "prix", "price")):
        return ("fill", "5")
    if any(k in label for k in _NAME_WORDS):
        return ("fill", entity_name)
    if field.get("required"):
        return ("fill", entity_name)
    return None


def generate_steps(project, nav_path=None, module="", functionality="",
                   extra_context="", url=""):
    """Generate the steps (CRUD when the screen has a form, simple
    navigation/verification otherwise) for a functionality.

    Returns (steps, error): steps is a list of instruction strings.  For CRUD
    screens the list includes the section labels 'CRÉATION', 'MODIFICATION',
    'SUPPRESSION' to mark each phase.  error is a friendly French message when
    generation is not possible.
    """
    page, session, stop = open_screen(project, nav_path=nav_path,
                                      module=module,
                                      functionality=functionality, url=url)
    if page is None:
        return [], ("Impossible d'ouvrir l'écran — génération automatique des "
                    "étapes indisponible.")
    try:
        return _generate_from_page(page, functionality)
    finally:
        try:
            session.close()
        except Exception:
            pass
        try:
            stop()
        except Exception:
            pass


def _navigation_steps(functionality, table_present=False):
    """Steps for a read-only screen: open the page and verify it loads."""
    steps = [
        {"action_type": "VERIFIER", "target": "la page se charge sans erreur",
         "value": "", "expected": "Page chargée"},
    ]
    if table_present:
        steps.append({"action_type": "VERIFIER",
                      "target": "le tableau de données est affiché",
                      "value": "", "expected": "Tableau visible"})
    return steps


def _generate_from_page(page, functionality, suffix=None):
    """Inspect a live screen (an already-open page) and produce the right
    steps: full CRUD when a creation form + editable fields are present,
    simple navigation + page-load verification for read-only screens.

    Reused by ``generate_steps`` and by the whole-app module scan so that the
    browser is only opened once.  Does NOT close the session — the caller owns
    the page lifecycle.

    Returns (steps, error) where steps is a list of structured action dicts.
    """
    if suffix is None:
        suffix = _suffix()
    word = _norm(functionality or "Item").split()
    entity = f"QA_{word[0].upper()}_{suffix}" if word else f"QA_{suffix}"
    entity_mod = f"{entity}_MOD"

    must_click_create = has_create_button(page)
    table_present = has_table(page)

    form_el = None
    if must_click_create:
        if click_create_button(page):
            form_el = find_form(page)
    if not form_el:
        form_el = find_form(page)

    # --- Read-only screen ---
    if not form_el:
        return _navigation_steps(functionality, table_present), None

    fields = [f for f in describe_form(form_el) if f.get("label")]

    # --- Form without named fields ---
    if not fields:
        if not must_click_create:
            return _navigation_steps(functionality, table_present), None
        steps = [
            {"action_type": "CLIQUEER", "target": "Ajouter",
             "value": "", "expected": "Formulaire ouvert"},
        ]
        return steps, None

    # --- Phase 1: CREATION ---
    create_steps = []
    if must_click_create:
        create_steps.append(
            {"action_type": "CLIQUEER", "target": "Ajouter",
             "value": "", "expected": "Formulaire ouvert"})
    for f in fields:
        res = _field_value(f, suffix, entity)
        if res is None:
            continue
        kind, fval = res
        if kind == "check":
            create_steps.append(
                {"action_type": "COCHER", "target": f["label"],
                 "value": "", "expected": ""})
        elif kind == "select":
            create_steps.append(
                {"action_type": "SELECTIONNER", "target": f["label"],
                 "value": fval, "expected": ""})
        else:
            create_steps.append(
                {"action_type": "REMPLIR", "target": f["label"],
                 "value": fval, "expected": ""})
    if not create_steps:
        return _navigation_steps(functionality, table_present), None
    save_text = submit_label(form_el)
    create_steps.append(
        {"action_type": "CLIQUEER", "target": save_text,
         "value": "", "expected": "Formulaire soumis"})
    if table_present:
        create_steps.append(
            {"action_type": "VERIFIER", "target": f"{entity} visible dans la liste",
             "value": "", "expected": "Élément créé"})

    # --- Phase 2: MODIFICATION ---
    edit_steps = []
    has_edit, has_delete = _detect_row_actions(page)
    if has_edit and table_present:
        edit_steps.append(
            {"action_type": "RECHERCHER", "target": entity,
             "value": "", "expected": "Élément trouvé"})
        edit_steps.append(
            {"action_type": "SELECTIONNER_LIGNE", "target": entity,
             "value": "", "expected": "Ligne sélectionnée"})
        edit_steps.append(
            {"action_type": "CLIQUEER", "target": "Modifier",
             "value": "", "expected": "Formulaire de modification ouvert"})
        for f in fields:
            res = _field_value(f, suffix, entity_mod)
            if res is None:
                continue
            kind, fval = res
            if kind == "check":
                edit_steps.append(
                    {"action_type": "COCHER", "target": f["label"],
                     "value": "", "expected": ""})
            elif kind == "select":
                edit_steps.append(
                    {"action_type": "SELECTIONNER", "target": f["label"],
                     "value": fval, "expected": ""})
            else:
                edit_steps.append(
                    {"action_type": "REMPLIR", "target": f["label"],
                     "value": fval, "expected": ""})
        if edit_steps:
            edit_steps.append(
                {"action_type": "CLIQUEER", "target": save_text,
                 "value": "", "expected": "Modification enregistrée"})
            edit_steps.append(
                {"action_type": "VERIFIER",
                 "target": f"{entity_mod} visible",
                 "value": "", "expected": "Modification confirmée"})

    # --- Phase 3: SUPPRESSION ---
    delete_steps = []
    if has_delete and table_present:
        delete_steps.append(
            {"action_type": "CLIQUEER", "target": "Supprimer",
             "value": "", "expected": "Demande de confirmation"})
        if _detect_confirm_button(page):
            delete_steps.append(
                {"action_type": "CLIQUEER", "target": "Confirmer",
                 "value": "", "expected": "Suppression effectuée"})
        delete_steps.append(
            {"action_type": "VERIFIER",
             "target": f"{entity_mod} absent de la liste",
             "value": "", "expected": "Élément supprimé"})

    # --- Assemble all phases ---
    steps = []
    steps.append({"action_type": "SECTION", "target": "CRÉATION",
                  "value": "", "expected": ""})
    steps.extend(create_steps)
    if edit_steps:
        steps.append({"action_type": "SECTION", "target": "MODIFICATION",
                      "value": "", "expected": ""})
        steps.extend(edit_steps)
    if delete_steps:
        steps.append({"action_type": "SECTION", "target": "SUPPRESSION",
                      "value": "", "expected": ""})
        steps.extend(delete_steps)

    return steps, None


# ---------------------------------------------------------------------------
# Whole-app / module scanning: discover modules & functionalities and write
# the CRUD scenarios in a single browser pass.
# ---------------------------------------------------------------------------

_GENERIC_MENU_WORDS = (
    "accueil", "home", "tableau de bord", "dashboard", "tableau du bord",
    "logout", "deconnexion", "déconnexion", "sign out", "profil", "profile",
    "mon compte", "mon profil", "settings", "paramètres", "parametres",
    "réglages", "reglages", "aide", "help", "notifications", "recherche",
    "search", "documentation", "favoris", "favorites", "administration",
)

# URL path segments that never correspond to a module or functionality.
_SKIP_URL_SEGMENTS = ("login", "logout", "signin", "signout", "register",
                      "inscription", "connexion", "deconnexion", "password",
                      "motdepasse", "oubli", "recovery", "forgot", "reset",
                      "help", "aide", "documentation", "support", "switch",
                      "profile", "mes", "my-account", "mon-compte", "theme",
                      "lang", "locale", "asset", "api", "auth",
                      "import-export")

# URL path segments at the END of a link that indicate an action, not a page.
_ACTION_URL_SEGMENTS = ("new", "create", "ajouter", "edit", "modifier",
                        "update", "delete", "supprimer", "add", "remove",
                        "save", "cancel", "import", "export", "download")


def _norm_path_segment(seg):
    """Lowercase a path segment and strip common separators so URL matching
    like '/crm/client-interaction' vs '/crm/client_interaction' works.
    Accepts either a plain segment string or a single-element list (callers
    sometimes pass a slice like `_path_segments(url)[:1]`)."""
    if isinstance(seg, (list, tuple)):
        seg = seg[0] if seg else ""
    s = (seg or "").strip("/").strip()
    s = s.replace("-", "").replace("_", "")
    return _norm(s)


def _path_segments(url):
    """Return the clean path segments of an absolute URL."""
    from urllib.parse import urlparse
    if not url:
        return []
    return [s for s in urlparse(url).path.split("/") if s]


def _strip_fragment(url):
    """Drop the #fragment part of a URL. A hash-only href like '.../#' must
    never be treated as a real page."""
    return (url or "").split("#", 1)[0]


def _same_host(url, base_url):
    from urllib.parse import urlparse
    if not url:
        return False
    try:
        u = urlparse(url)
        b = urlparse(base_url)
    except Exception:
        return url.startswith(base_url)
    return u.scheme in ("http", "https") and u.netloc == b.netloc


def _url_under_module(url, module_url):
    """True if url belongs to the module identified by module_url (same host
    and the path starts with the module's leading segment, e.g. /crm)."""
    from urllib.parse import urlparse
    if not url or not module_url or not _same_host(url, module_url):
        return False
    base_seg = _norm_path_segment(_path_segments(module_url)[:1])
    if not base_seg:
        # The module URL has no real path (host root or hash-only like
        # '.../#'): it cannot cover the whole host. Only root pages belong.
        return not _path_segments(url)
    return _norm_path_segment(_path_segments(url)[:1]) == base_seg


def _looks_like_action_url(url, module_url=None):
    """Filter out links that are CRUD actions and generic/utility pages."""
    segs = _path_segments(url)
    if not segs:
        return True
    norm = [_norm_path_segment(s) for s in segs]
    if any(s in _SKIP_URL_SEGMENTS for s in norm):
        return True
    # last segment = an action verb => action link, skip as a functionality
    if norm[-1] in _ACTION_URL_SEGMENTS:
        return True
    if module_url:
        base_seg = _norm_path_segment(_path_segments(module_url)[:1])
        # the module home itself is not a functionality
        if base_seg and norm == [base_seg]:
            return True
    return False


def _url_slug_to_label(url):
    """Derive a readable functionality label from its URL path, e.g.
    '/crm/client_interaction' -> 'Client interaction'."""
    segs = _path_segments(url)
    if not segs:
        return ""
    # drop the module prefix segment
    tail = segs[1:] if len(segs) > 1 else segs
    if not tail:
        return ""
    words = []
    for seg in tail:
        sub = seg.replace("_", " ").replace("-", " ").replace(".", " ")
        sub = " ".join(w for w in sub.split() if w)
        if sub:
            words.append(sub)
    label = " / ".join(words).strip()
    if not label:
        return segs[-1].replace("_", " ").replace("-", " ").strip()
    return label


def _clean_menu_label(label):
    return " ".join((label or "").split())


def _is_generic_menu(label):
    n = _norm(label)
    if not n or len(n) < 2:
        return True
    for w in _GENERIC_MENU_WORDS:
        if n == w or n.startswith(w) or w in n:
            return True
    return False


def _looks_like_crud_button(label):
    n = _norm(label)
    for w in _CREATE_WORDS + _SAVE_WORDS:
        if n == w or w in n:
            return True
    return False


def _nav_links(page):
    """Visible navigation links with their (absolute) hrefs.

    Returns a list of dicts {label, href, tag}. href is '' for buttons.
    Collapsed sidebar sections are harvested too (their links are present in
    the DOM, just hidden), so discovery never misses a module or function.
    """
    items = _scan_menu_items(page)
    out = []
    seen = set()
    for it in items:
        lbl = _clean_menu_label(it["label"])
        href = (it.get("href") or "").strip()
        key = (_norm(lbl), href)
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": lbl, "href": href, "tag": it.get("tag", "")})
    for sec in (_sidebar_tree(page) or []):
        if sec.get("href") and not sec.get("links"):
            key = (_norm(sec["name"]), sec["href"])
            if key not in seen:
                seen.add(key)
                out.append({"label": sec["name"], "href": sec["href"],
                            "tag": "a"})
        for ln in sec.get("links") or []:
            key = (_norm(ln["label"]), ln["href"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"label": ln["label"], "href": ln["href"], "tag": "a"})
    return out


def find_module_url(page, module_name, base_url=""):
    """Return the absolute URL of a module from its nav link (label or href
    match). Returns {} when not found. A link whose href has no real path
    segment (e.g. 'https://host/#' or 'https://host/') is never a module."""
    name_key = _norm_path_segment(module_name)
    links = _nav_links(page)
    for link in links:
        href = link["href"]
        if not href:
            continue
        seg = _norm_path_segment(_path_segments(href)[:1])
        if not name_key or not seg:
            continue
        if seg == name_key or name_key in seg or seg in name_key:
            return {"name": module_name, "url": _strip_fragment(href)}
    # Fallback: fuzzy match on the nav label + href
    best_score = 0
    best_href = ""
    for link in links:
        if not link["href"] or not _path_segments(link["href"]):
            continue
        score = _fuzzy_match(link["label"], module_name)
        if score > best_score:
            best_score = score
            best_href = link["href"]
    return {"name": module_name, "url": _strip_fragment(best_href) if best_score >= 40 else ""}


def discover_modules(page, base_url=""):
    """Detect the application's modules from the navigation, generically —
    nothing is hardcoded: everything comes from the scanned application.

    Modules are found from the actual menu structure:
      - Preferred: each top-level section of the sidebar tree is one module
        (an expandable section -> its home link URL + its child links;
        a plain top-level page -> a single-page module).
      - Fallback: when no sidebar tree is available, modules are grouped by
        the FIRST path segment of their navigation links.
      - Last resort: label-only discovery.

    Returns a list of dicts:
        [{"name": str, "url": str, "links": [{"label", "href"}, ...]}]
    where ``links`` are the module's own menu children (used afterwards to
    discover its functionalities, even when those live under a different
    URL prefix).
    """
    _expand_accordion_menus(page)
    page.wait_for_timeout(600)
    sections = _sidebar_tree(page)
    if sections:
        out = []
        prefixes = set()
        for sec in sections:
            name = _clean_menu_label(sec["name"])
            if not name:
                continue
            links = sec.get("links") or []
            url = ""
            if links:
                url = links[0]["href"]
                for ln in links:
                    segs = _path_segments(ln["href"])
                    if len(segs) == 1 and \
                            _norm_path_segment(segs[0]) not in _SKIP_URL_SEGMENTS:
                        url = ln["href"]
                        break
            else:
                url = (sec.get("href") or "").strip()
            url = _strip_fragment((url or "").split("?")[0])
            if not url or not _same_host(url, base_url or url):
                continue
            segs = _path_segments(url)
            if not segs or _looks_like_action_url(url):
                continue
            prefix = _norm_path_segment(segs[0])
            if not prefix or prefix in _SKIP_URL_SEGMENTS or \
                    prefix in prefixes:
                continue
            prefixes.add(prefix)
            out.append({
                "name": name,
                "url": url,
                "links": [{"label": ln.get("label", ""), "href": ln.get("href", "")}
                          for ln in links if (ln.get("href") or "").strip()],
            })
        if out:
            return out

    # Fallback: group nav links by their first path segment.
    by_prefix = {}   # prefix -> {"name", "url"}
    has_hrefs = False
    for link in _nav_links(page):
        href = link["href"]
        if not href:
            continue
        has_hrefs = True
        segs = _path_segments(href)
        if not segs:
            continue
        prefix = _norm_path_segment(segs[0])
        if not prefix or prefix in _SKIP_URL_SEGMENTS:
            continue
        if _GENERIC_MENU_WORDS and any(
                prefix == _norm_path_segment(w) for w in _GENERIC_MENU_WORDS):
            continue
        url = href
        if prefix not in by_prefix:
            by_prefix[prefix] = {"name": "", "url": url}
        if len(segs) == 1 and not by_prefix[prefix]["name"]:
            by_prefix[prefix]["name"] = _clean_menu_label(link["label"])

    out = []
    for prefix, info in by_prefix.items():
        name = info["name"] or _url_slug_to_label(info["url"]) or prefix.title()
        out.append({"name": name.strip(), "url": info["url"], "links": []})

    if not has_hrefs:
        # Last-resort: label-only discovery
        seen = set()
        for it in _scan_menu_items(page):
            lbl = _clean_menu_label(it["label"])
            key = _norm(lbl)
            if not key or key in seen:
                continue
            if _is_generic_menu(lbl):
                continue
            if _looks_like_crud_button(lbl):
                continue
            seen.add(key)
            out.append({"name": lbl, "url": "", "links": []})
    return out


def discover_functionalities(page, module_url, module_label="", module_links=None):
    """Discover the functionalities of a module — generically, so it works on
    any application: nothing is hardcoded, everything comes from the scan.

    Three complementary sources feed the list:
      1. module_links: the module's own menu children harvested from the
         sidebar tree. They are always listed even when their URL lives
         outside the module's path prefix.
      2. navigation links under the module's URL prefix (catches sub-pages
         that do not appear in the sidebar).
      3. every anchor visible in the module home page (catches card /
         dashboard links that navigate elsewhere).

    Returns a list of dicts [{"name": str, "url": str}]. The module's own
    home URL is never a functionality (it is scanned as the module page).
    """
    _expand_accordion_menus(page)
    page.wait_for_timeout(600)
    candidates = []
    home_url = _strip_fragment((module_url or "").split("?")[0])

    def collect(href, label="", origin="page"):
        href = (href or "").strip()
        clean = _strip_fragment(href.split("?")[0])
        if not href or not clean or clean == home_url:
            return
        if not _same_host(href, module_url or href):
            return
        if not _url_under_module(href, module_url):
            return
        if _looks_like_action_url(href, module_url):
            return
        name = _clean_menu_label(label) or _url_slug_to_label(href) \
            or href.rsplit("/", 1)[-1]
        candidates.append((clean, name, origin))

    # 1. The module's own sidebar children (structure — regardless of prefix).
    for ln in (module_links or []):
        href = (ln.get("href") or "").strip()
        clean = _strip_fragment(href.split("?")[0])
        if not href or not clean or clean == home_url:
            continue
        if not _same_host(href, module_url or href):
            continue
        if _looks_like_action_url(clean, module_url):
            continue
        name = _clean_menu_label(ln.get("label") or "") \
            or _url_slug_to_label(clean) or clean.rsplit("/", 1)[-1]
        candidates.append((clean, name, "module"))

    # 2. Navigation links under the module's URL prefix.
    for link in _nav_links(page):
        collect(link["href"], link["label"], "nav")

    # 3. Also scan ALL visible anchors on the current page (catches links not
    # present in the persistent sidebar, e.g. cards leading to subpages).
    try:
        page.evaluate("() => { window.__scanAllHrefs = Array.from("
                      "document.querySelectorAll('a[href]')).map(a => a.href); }")
        for href in (page.evaluate("() => window.__scanAllHrefs") or []):
            collect(href)
    except Exception:
        pass

    _ORIGIN_RANK = {"module": 3, "nav": 2, "page": 1}

    def _quality(name, origin):
        # Prefer a menu-built label (module structure, then sidebar) over an
        # in-page slug, then a label with real words, then the longest one.
        name = (name or "").strip()
        return (_ORIGIN_RANK.get(origin, 0), " " in name, len(name))

    # One functionality per URL: when the same page is found through several
    # links (sidebar + in-page cards), keep the richest label ("Tous les
    # Clients" rather than the URL slug "clients").
    best_name = {}
    for href, name, origin in candidates:
        clean = _strip_fragment(href.split("?")[0])
        if not clean:
            continue
        prev = best_name.get(clean)
        if prev is None or _quality(name, origin) > _quality(*prev):
            best_name[clean] = (name, origin)

    out = []
    for clean, (name, _origin) in best_name.items():
        out.append({"name": name, "url": clean})
    return out


def _fallback_steps(functionality):
    """Fallback steps used when live inspection fails for a discovered
    functionality: a simple navigation + page-load verification scenario, so
    a page that has no action (or that we could not inspect) is never forced
    into a CRUD workout."""
    return [
        {"action_type": "VERIFIER",
         "target": f"la page {functionality} se charge sans erreur",
         "value": "", "expected": "Page chargée"},
    ]


def _goto_url(page, url):
    """Navigate to an absolute URL and settle. Returns True on success."""
    try:
        page.goto(url, timeout=45000)
        browser.settle(page)
        page.wait_for_timeout(500)
        return True
    except Exception:
        try:
            page.goto(url, wait_until="commit", timeout=60000)
            browser.settle(page)
            page.wait_for_timeout(500)
            return True
        except Exception:
            return False


def _page_is_blank(page):
    """True when the current page is effectively blank/white: almost no text
    and no interactive element. Such pages must never trap the scan — the
    caller moves back to the module home and carries on."""
    try:
        text_len = page.evaluate(
            "() => (document.body ? (document.body.innerText || '') "
            ".trim().length : 0)")
        el_count = page.evaluate(
            "() => (document.body ? document.body.querySelectorAll("
            "'input,button,select,textarea,a,table,img').length : 0)")
        return text_len < 40 and el_count < 2
    except Exception:
        return False


def _generate_for_functionality(page, module_name, fname, furl, suffix,
                                control=None):
    """Generate steps for one functionality, preferring direct URL
    navigation (reliable) over menu-based auto-discovery.  When navigation
    itself fails (or the screen renders blank), the functionality is still
    registered with a simple navigation scenario so a read-only page is never
    forced into a CRUD workout. The caller is responsible for moving back to
    the module home after a blank page."""
    if control is not None:
        control.check()
    if furl:
        if not _goto_url(page, furl):
            _log("scan: navigation impossible vers '%s' (%s)" % (fname, furl))
            return _fallback_steps(fname)
        if _page_is_blank(page):
            page.wait_for_timeout(1500)
            if _page_is_blank(page):
                _log("scan: page blanche détectée sur '%s' (%s)" %
                     (fname, furl))
                return _fallback_steps(fname)
        steps, err = _generate_from_page(page, fname, suffix=suffix)
        if err:
            _log("scan: '%s' -> %s" % (fname, err))
            steps = _fallback_steps(fname)
        return steps
    # No direct URL: try auto-discovery by walking the module menu
    if _auto_discover_and_navigate(page, module_name, fname):
        if _page_is_blank(page):
            page.wait_for_timeout(1500)
            if _page_is_blank(page):
                _log("scan: page blanche après navigation vers '%s'" % fname)
                return _fallback_steps(fname)
        steps, err = _generate_from_page(page, fname, suffix=suffix)
        if err:
            steps = _fallback_steps(fname)
        return steps
    _log("scan: navigation impossible vers '%s'" % fname)
    return _fallback_steps(fname)


def _safe_generate_from_page(page, functionality, suffix):
    """Generate steps for one screen, never raising on a transient browser
    /DOM error (stale element, page re-render mid-scan, ...). A single screen
    failing must not abort scanning the whole module: fall back to simple
    navigation steps so the functionality still gets a runnable scenario
    without forcing CRUD on a page that may have no data entry."""
    try:
        steps, err = _generate_from_page(page, functionality, suffix=suffix)
        if err:
            return _fallback_steps(functionality), None
        return steps, None
    except Exception as e:
        _log("scan: génération de '%s' ignorée (%s)" % (functionality, e))
        return _fallback_steps(functionality), None


def _safe_generate_functionality(page, module_name, fname, furl, suffix,
                                 control=None):
    try:
        if control is not None:
            return _generate_for_functionality(page, module_name, fname, furl,
                                               suffix, control=control)
        return _generate_for_functionality(page, module_name, fname, furl,
                                           suffix)
    except ScanCancelled:
        raise
    except Exception as e:
        _log("scan: fonctionnalité '%s' générée en secours (%s)" % (fname, e))
        return _fallback_steps(fname)


def _build_module_url(base_url, module_name):
    """Derive a module's landing URL from the project base URL plus the
    module's name.  A module "IMS" on https://kpip.kprimesoft.com/ targets
    https://kpip.kprimesoft.com/ims first — the QA must land inside the
    folder before anything is scanned.  If the module name is already an
    absolute URL or a full path like "crm/clients", it is kept as given.
    """
    name = (module_name or "").strip().strip("/")
    if name.lower().startswith(("http://", "https://")):
        return _strip_fragment(name.split("?")[0])
    base = _strip_fragment(base_url or "").strip().strip("?").rstrip("/")
    if not name:
        return ""
    segs = [s for s in name.split("/") if s]
    slug = "/".join(_slugify(s) for s in segs)
    return "%s/%s" % (base, slug) if base else slug


def _slugify(seg):
    s = (seg or "").lower().strip()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("â", "a"),
                 ("î", "i"), ("ï", "i"), ("û", "u"), ("ô", "o"),
                 ("ç", "c"), ("â", "a")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _scan_module_using_session(page, project, module, module_url="",
                               nav_path=None, control=None):
    """Scan ONE module in an already-open session (single browser pass).

    Before anything is scanned, the browser is pointed DIRECTLY at the
    module's landing URL (e.g. https://kpip.kprimesoft.com/crm), using in
    order: the stored module URL, the navigation href, or base_url + module
    name.  The module's own page is the FIRST page scanned, then only the
    links under that URL prefix are considered functionalities (the
    dashboard and other modules are ignored).

    If the browser does not land inside the module (redirect to login, HTTP
    error, ...), nothing is scanned and an error message is returned.

    Returns (results, error):
      results = [{"functionality": str, "url": str, "path": str, "steps": [str]}]
      error   = None or a friendly French message (in which case results=[]).
    """
    if control is not None:
        control.check()
    module_name = module["name"] if isinstance(module, dict) else module
    if isinstance(module, dict):
        module_url = module.get("url") or module_url

    # 1) stored URL / nav href  →  2) project base + module name.
    # A stored or nav URL without a real path segment (host root or
    # hash-only '.../#') is NEVER accepted: it would make every page of the
    # host look like it belongs to the module (the '# / 93' bug).
    if not module_url or not _path_segments(module_url):
        module_url = ""
        found = find_module_url(page, module_name, project.get("url", ""))
        found_url = (found.get("url") or "").strip()
        if found_url and _path_segments(found_url):
            module_url = _strip_fragment(found_url.split("?")[0])

    if not module_url or not _path_segments(module_url):
        module_url = _build_module_url(project.get("url", ""), module_name)

    if not module_url:
        return [], ("Impossible de déterminer l'URL du module '%s' — "
                    "renseignez-la dans Modifier le module." % module_name)

    _log("scan module '%s' -> %s (première page à scanner)" %
         (module_name, module_url))
    if not _goto_url(page, module_url):
        return [], ("Navigation impossible vers l'URL du module '%s' (%s)." %
                    (module_name, module_url))

    # Verify we really are inside the module before scanning anything.
    if not _url_under_module(page.url, module_url):
        return [], ("Scan annulé : la page ouverte par le navigateur (%s) "
                    "n'appartient pas au module '%s' (%s). Rien n'a été "
                    "enregistré." % (page.url, module_name, module_url))

    # Remember the verified URL so a future scan goes straight to it.
    if isinstance(module, dict):
        module["url"] = module_url

    page.wait_for_timeout(600)
    suffix = _suffix()
    results = []

    # The module's own page is the FIRST page scanned (its URL is the module
    # URL).  It always appears first in the result list.
    home_steps, home_err = _safe_generate_from_page(page, module_name, suffix)
    results.append({"functionality": module_name, "url": module_url,
                    "path": "", "steps": home_steps})

    try:
        module_links = module.get("links") if isinstance(module, dict) else None
        if module_links:
            fns = discover_functionalities(page, module_url, module_name,
                                           module_links=module_links)
        else:
            fns = discover_functionalities(page, module_url, module_name)
    except Exception as e:
        _log("scan module '%s': découverte impossible (%s)" %
             (module_name, e))
        fns = []
    _log("scan module '%s': %d fonctionnalité(s) détectée(s)" %
         (module_name, len(fns)))
    for f in fns[:20]:
        _log("  - %s (%s)" % (f["name"], f["url"]))

    total = len(fns)
    for i, f in enumerate(fns):
        if control is not None:
            control.check()
        _log("scan '%s' -> fonctionnalité %d/%d : '%s' (%s)" %
             (module_name, i + 1, total, f["name"], f["url"]))
        steps = _safe_generate_functionality(page, module_name, f["name"],
                                             f["url"], suffix,
                                             control=control)
        # A screen that rendered blank must never trap the scan: go back to
        # the project's home page (the URL configured on the project) and
        # carry on with the next functionality.
        if _page_is_blank(page):
            home_url = project.get("url", "")
            _log("scan module '%s': page blanche après '%s' — retour à la "
                 "page d'accueil du projet (%s) pour continuer" %
                 (module_name, f["name"], home_url or "URL du projet"))
            if home_url:
                _goto_url(page, home_url)
        results.append({"functionality": f["name"], "url": f["url"],
                        "path": "", "steps": steps})
    return results, None


def scan_module(project, module, nav_path=None, control=None):
    """Open the module's screen (direct URL, verified) and auto-discover its
    functionalities with their CRUD scenarios, all in one browser pass.

    module may be a module dict {name, url} or a plain name string.

    Returns (results, error):
      results = [{"functionality", "url", "path", "steps"}]
      error   = None or a friendly French message (results=[] then).
    """
    module_name = module["name"] if isinstance(module, dict) else module
    _log("scan_module: '%s' nav_path=%s" % (module_name, nav_path))
    page, session, stop = open_screen(project, nav_path=nav_path,
                                      module=module_name, functionality="")
    if page is None:
        return [], ("Impossible d'ouvrir l'écran — scanner le module "
                    "indisponible.")
    try:
        return _scan_module_using_session(page, project, module,
                                          nav_path=nav_path, control=control)
    finally:
        try:
            session.close()
        except Exception:
            pass
        try:
            stop()
        except Exception:
            pass


def scan_whole_app(project, on_module_progress=None, control=None):
    """Scan the whole application without any user-provided module.

    Opens one browser session, reads the top-level navigation to detect the
    modules (grouped by URL prefix so the URL always points to the module
    first, e.g. /crm), then for each module discovers its sub-pages and
    writes their CRUD scenarios — all in a single pass.

    on_module_progress(module_name, func_count, total_func_count) is called
    after each module for progress reporting.

    control: optional ScanControl. When the user stops the scan, ScanCancelled
    is raised at the module boundary; no data is written for that run.

    Returns (modules, error):
      modules = [{"module": str, "url": str,
                  "functionalities": [{"functionality", "url", "path", "steps"}]}]
      error   = None or a friendly French message.
    """
    if control is not None:
        control.check()
    _log("scan_whole_app: opening session")
    from .browser import open_session
    session, stop = open_session()
    page = session.page
    try:
        try:
            session.goto(project["url"])
        except Exception:
            session.goto(project["url"], wait_until="commit", timeout=60000)
        browser.settle(page)
        if project.get("auth_type", "none") != "none" and has_login_form(page):
            try_login(page, project.get("email", ""),
                      security.decrypt_value(project.get("password_enc", "")))
            browser.settle(page)
        if control is not None:
            control.check()
        modules = discover_modules(page, project.get("url", ""))
        _log("scan_whole_app: %d module(s) détecté(s)" % len(modules))
        for m in modules:
            _log("  module: '%s' (%s)" % (m["name"], m.get("url")))
        if not modules:
            return [], ("Aucun module détecté dans la navigation de "
                        "l'application.")
        total = 0
        out = []
        for idx, m in enumerate(modules):
            if control is not None:
                control.check()
            _log("scan_whole_app: module %d/%d : '%s'" % (idx + 1,
                                                          len(modules),
                                                          m["name"]))
            fns, err = _scan_module_using_session(page, project, m,
                                                  nav_path=[],
                                                  control=control)
            if err:
                _log("scan_whole_app: module '%s' ignoré : %s" %
                     (m["name"], err))
                continue
            out.append({"module": m["name"], "url": m.get("url", ""),
                        "functionalities": fns})
            total += len(fns)
            if on_module_progress:
                try:
                    on_module_progress(m["name"], len(fns), total)
                except Exception:
                    pass
        if not out:
            return [], ("Aucun module n'a pu être scanné : les URL des "
                        "modules n'ont pas pu être vérifiées (navigation en "
                        "échec ou page hors module).")
        return out, None
    finally:
        try:
            session.close()
        except Exception:
            pass
        try:
            stop()
        except Exception:
            pass