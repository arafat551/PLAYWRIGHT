"""Explorer: walks the application like a QA analyst (top-down), inventories
the real interactive elements (buttons, forms, searches, tables, actions
columns) and records a stable cartography.

Button naming priority (spec section 14):
  1. visible text  2. aria-label  3. title  4. value  5. icon  6. fallback

Interactive elements are ordered by visual position: Y then X (section 12).
"""
from urllib.parse import urlparse, urlunparse
import re

from ..config import env, setting_int
from .browser import has_login_form, try_login
from .classification import (ICON_LABELS, normalize,
                             CREATE_HINTS, DELETE_HINTS, UPDATE_HINTS,
                             SEARCH_HINTS, STATUS_HINTS, READ_HINTS)

LOGOUT_HINTS = ("logout", "log-out", "log_out", "signout", "sign-out",
                "deconnexion", "déconnexion", "deconnecter", "se-deconnecter",
                "se déconnecter", "quitter", "sign out")

FILE_EXT_SKIP = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf",
                 ".zip", ".rar", ".css", ".js", ".json", ".xml", ".woff",
                 ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".webp")

ACTION_COLUMN_HINTS = ("actions", "action", "options", "opérations", "operations", "opcao")

BTN_CSS = ('button, [role="button"], input[type="submit"], '
           'input[type="button"], a[role="menuitem"]')

# Anchors that realistically act as buttons (styled as buttons, or icon-only)
BTN_ANCHOR_CSS = ('a.btn, a[class*="btn"], a[class*="button"], a[class*="action"], '
                  'a[data-action], a[aria-haspopup], a[onclick]')

SEARCH_CSS = ('input[type="search"], input[name*="search" i], '
              'input[name*="query" i], input[name*="recherch" i], '
              'input[placeholder*="recherch" i], input[placeholder*="search" i]')

# Words that make a plain <a href> look like an action button rather than a
# simple navigation link (e.g. row actions in a datatable, toolbar creations).
_ACTION_HINTS = (CREATE_HINTS + DELETE_HINTS + UPDATE_HINTS + SEARCH_HINTS +
                 STATUS_HINTS + READ_HINTS + (
                     "export", "exporter", "import", "importer", "imprimer",
                     "telecharger", "télécharger", "download", "dupliquer",
                     "cloner", "copier", "valider", "approuver", "refuser",
                     "publier", "resilier", "résilier", "annuler", "abonner",
                     "payer", "relancer", "assigner", "verrouiller", "changer",
                     "transferer", "transférer", "signer", "voir", "ouvrir"))

_HREF_HINTS = ("/new", "/add", "/create", "/edit", "/delete", "/update",
               "/remove", "/trash", "/archive", "/enable", "/disable",
               "/export", "/import", "/download", "=create", "=edit", "=new",
               "=delete", "=export")


def icon_label(text):
    t = (text or "").strip()
    if not t:
        return ""
    # Handle small icon-only buttons where the inner text is an emoji/symbol
    return ICON_LABELS.get(t, "")


# Icon-font/utility class fragments -> CRUD meaning. Used when a button/link
# has no readable text (FontAwesome, Bootstrap icons, Material, Feather, ...).
ICON_CLASS_MAP = (
    ("plus-circle", "Ajouter"), ("user-plus", "Ajouter"), ("plus", "Ajouter"),
    ("pencil-alt", "Modifier"), ("pencil", "Modifier"), ("edit", "Modifier"),
    ("trash-alt", "Supprimer"), ("trash-o", "Supprimer"), ("trash", "Supprimer"),
    ("delete", "Supprimer"), ("remove", "Supprimer"),
    ("eye", "Visualiser"), ("search", "Recherche"), ("zoom", "Recherche"),
    ("magnify", "Recherche"),
    ("download", "Exporter"), ("file-export", "Exporter"), ("export", "Exporter"),
    ("upload", "Importer"), ("file-import", "Importer"), ("import", "Importer"),
    ("toggle-off", "Désactiver"), ("toggle on", "Activer"), ("toggle-on", "Activer"),
    ("ban", "Désactiver"), ("minus-circle", "Désactiver"), ("slash", "Désactiver"),
    ("toggle", "Désactiver"),
    ("check-circle", "Valider"), ("check", "Valider"), ("ok", "Valider"),
    ("done", "Valider"),
    ("printer", "Imprimer"), ("print", "Imprimer"),
    ("copy", "Dupliquer"), ("clone", "Dupliquer"), ("duplicate", "Dupliquer"),
    ("archive", "Archiver"), ("inbox", "Archiver"),
    ("send", "Envoyer"), ("paper-plane", "Envoyer"),
    ("lock", "Verrouiller"), ("unlock", "Déverrouiller"),
    ("user-plus", "Ajouter"), ("user-add", "Ajouter"),
    ("refresh", "Actualiser"), ("sync", "Actualiser"),
)

_ICON_RE = [(re.compile(r"\b" + re.escape(tok) + r"\b"), lab)
            for tok, lab in ICON_CLASS_MAP]


def _icon_class_label(el):
    """Guess a CRUD label from icon-font/utility CSS classes of the element
    and its children (e.g. `<i class="fa fa-pencil"></i>` -> 'Modifier')."""
    try:
        classes = el.evaluate(
            """e => {
                const out = [];
                const walk = n => {
                    if (n && n.nodeType === 1 && n.getAttribute) {
                        const cl = (n.getAttribute('class') || '') + ' ' +
                                   (n.getAttribute('data-icon') || '');
                        if (cl.trim()) out.push(cl);
                        for (let c of (n.children || [])) walk(c);
                    }
                };
                walk(e);
                return out.join(' ');
            }""")
    except Exception:
        return ""
    n = (classes or "").lower().replace("-", " ").replace("_", " ")
    n = n.replace("/", " ").replace("\\", " ").replace(".", " ")
    for rx, lab in _ICON_RE:
        if rx.search(n):
            return lab
    return ""


def _visible_text(el):
    try:
        return (el.inner_text() or "").strip()
    except Exception:
        return ""


def _attr(el, name):
    try:
        return (el.get_attribute(name) or "").strip()
    except Exception:
        return ""


def element_label(el, idx=0):
    """Real human-readable label for a button/link (see priorities above)."""
    text = _visible_text(el)
    if text and not _only_icon(text):
        # strip trailing '...' and newlines; collapse whitespace
        label = " ".join(text.split())
        return label[:80]
    aria = _attr(el, "aria-label")
    if aria:
        return aria[:80]
    title = _attr(el, "title")
    if title:
        return title[:80]
    value = _attr(el, "value")
    if value:
        return value[:80]
    if text and _only_icon(text):
        lab = icon_label(text)
        if lab:
            return lab
    data_tip = _attr(el, "data-tooltip") or _attr(el, "data-tip") or _attr(el, "data-original-title")
    if data_tip:
        return data_tip[:80]
    lab = _icon_class_label(el)
    if lab:
        return lab
    return f"Bouton #{idx + 1}"


def _only_icon(text):
    return len(text) <= 4 and not any(c.isalnum() for c in text.replace(" ", ""))


def _element_pos(el):
    try:
        box = el.bounding_box()
        if box:
            return (round(box["y"], 1), round(box["x"], 1))
    except Exception:
        pass
    try:
        rect = el.evaluate("e => {const r=e.getBoundingClientRect(); return [r.top+window.scrollY, r.left+window.scrollX]}")
        return (round(rect[0], 1), round(rect[1], 1))
    except Exception:
        return (99999, 99999)


def _is_same_domain(href, base_netloc):
    try:
        return urlparse(href).netloc == base_netloc
    except Exception:
        return False


def _clean_url(href):
    u = urlparse(href)
    return urlunparse((u.scheme, u.netloc, u.path, "", "", "")).rstrip("/")


def _lower(href):
    return href.lower()


def settle(page):
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass
    try:
        page.wait_for_timeout(600)
    except Exception:
        pass


def scroll_full(page):
    try:
        page.evaluate("""async () => {
            await new Promise(resolve => {
                let total = 0;
                const step = () => {
                    window.scrollBy(0, 700);
                    total += 700;
                    const max = document.body.scrollHeight;
                    if (total >= max || total > 40000) { window.scrollTo(0,0); resolve(); }
                    else setTimeout(step, 100);
                };
                step();
            });
        }""")
        page.wait_for_timeout(400)
    except Exception:
        pass


def _page_title(page, url):
    try:
        h1 = page.query_selector("h1")
        if h1 and h1.inner_text().strip():
            return " ".join(h1.inner_text().split())[:80]
    except Exception:
        pass
    try:
        t = page.title() or ""
        if t.strip():
            return t.strip()[:80]
    except Exception:
        pass
    return urlparse(url).path.strip("/") or "Accueil"


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------

def scan_buttons(page):
    """Inventory buttons ordered by Y then X. Returns list of dicts.

    Includes <button> elements plus anchors that act as buttons (styled as
    buttons or icon-only links, e.g. row action icons / 'Ajouter' links).
    """
    els = []
    seen = set()
    results = []
    try:
        els = page.query_selector_all(BTN_CSS)
    except Exception:
        els = []
    try:
        anchors = page.query_selector_all(BTN_ANCHOR_CSS)
    except Exception:
        anchors = []
    try:
        links = page.query_selector_all("a[href]")
    except Exception:
        links = []

    # Build a combined candidate list; keep position ordering by Y then X.
    for el in els:
        try:
            results.append((el, element_label(el)))
        except Exception:
            continue
    for el in anchors:
        try:
            # skip simple navigation anchors unless icon-only or button-like text
            label = element_label(el)
            if label.startswith("Bouton #"):
                continue
            results.append((el, label))
        except Exception:
            continue
    for el in links:
        try:
            label = element_label(el)
            if _is_action_anchor(label, el):
                results.append((el, label))
        except Exception:
            continue

    found = []
    for idx, (el, label) in enumerate(results):
        if not label:
            continue
        if _is_logout(label):
            continue
        if label in seen:
            continue
        seen.add(label)
        y, x = _element_pos(el)
        # Only keep anchors if they are visible (actual action links),
        # unless they carry an explicit action signature (icon-only links).
        try:
            is_a = (el.evaluate("n => n.tagName") or "").lower() == "a"
            if is_a and not el.is_visible() and not _is_action_signature(el):
                continue
        except Exception:
            pass
        found.append({
            "action_type": "button",
            "label": label,
            "x": x,
            "y": y,
        })
    found.sort(key=lambda a: (a["y"], a["x"]))
    cap = max(0, setting_int("max_buttons", 300))
    found = found[:cap]
    for i, a in enumerate(found):
        a["el_index"] = i
    return found


def _is_logout(label):
    return any(h in label.lower() for h in LOGOUT_HINTS)


def _is_action_signature(el):
    """Does the element carry an explicit action signal (script, accessible
    label, button/action class)? Icon-only links with an empty glyph have a
    zero-size box, so visibility alone must not disqualify them."""
    try:
        cls = _lower(_attr(el, "class") or "")
        if any(k in cls for k in ("btn", "action", "button", "icon")):
            return True
    except Exception:
        pass
    return bool(_attr(el, "onclick") or _attr(el, "data-action")
                or _attr(el, "aria-label") or _attr(el, "title"))


def _is_action_anchor(label, el):
    """Decide whether a plain <a href> acts as an action button (toolbar
    'Ajouter' links, datatable row actions like Modifier/Supprimer, exports...)
    instead of a plain navigation link."""
    if not label or label.startswith("Bouton #"):
        return False
    if len(label) > 40:
        return False
    n = normalize(label)
    if any(h in n for h in _ACTION_HINTS):
        return True
    href = _lower(_attr(el, "href") or "")
    if any(h in href for h in _HREF_HINTS):
        return True
    # explicit accessible label (aria-label / title) on a button-styled or
    # scripted element (row action icons, confirm dialogs) => action
    cls = _lower(_attr(el, "class") or "")
    if _attr(el, "onclick"):
        return True
    if any(k in cls for k in ("btn", "action", "button", "dropdown", "icon", "icon-btn")) and \
            (_attr(el, "aria-label") or _attr(el, "title")):
        return True
    return False


# ---------------------------------------------------------------------------
# Forms / search / tables
# ---------------------------------------------------------------------------

def scan_search_fields(page):
    fields = []
    try:
        els = page.query_selector_all(SEARCH_CSS)
    except Exception:
        return fields
    for idx, el in enumerate(els):
        y, x = _element_pos(el)
        ph = _attr(el, "placeholder")
        label = ph or _attr(el, "aria-label") or "Recherche"
        fields.append({
            "action_type": "search",
            "label": label[:80],
            "x": x,
            "y": y,
            "el_index": idx,
        })
    fields.sort(key=lambda a: (a["y"], a["x"]))
    return fields


def scan_forms(page):
    forms = []
    try:
        els = page.query_selector_all("form")
    except Exception:
        return forms
    for idx, el in enumerate(els):
        try:
            if not el.is_visible():
                continue
            y, x = _element_pos(el)
            id_attr = _attr(el, "id") or _attr(el, "name") or ""
            label = id_attr or f"Formulaire"
            forms.append({
                "action_type": "form",
                "label": label[:80],
                "x": x,
                "y": y,
                "el_index": idx,
            })
        except Exception:
            continue
    forms.sort(key=lambda a: (a["y"], a["x"]))
    cap = max(0, setting_int("max_forms", 30))
    return forms[:cap]


def scan_tables(page):
    """Detect tables + their action columns. Returns list of dicts."""
    tables = []
    try:
        els = page.query_selector_all("table")
    except Exception:
        return tables
    for idx, el in enumerate(els):
        try:
            header = _table_header(el)
            y, x = _element_pos(el)
            tables.append({
                "action_type": "table",
                "label": header or f"Tableau #{idx + 1}",
                "header": header,
                "action_column": _is_action_table(el, header),
                "x": x,
                "y": y,
                "el_index": idx,
                "row_actions": _row_actions(el),
            })
        except Exception:
            continue
    return tables


def _table_header(el):
    try:
        th = el.query_selector_all("thead th")
        if th:
            cells = [th[i].inner_text().strip() for i in range(min(3, len(th))) if th[i].inner_text().strip()]
            return " | ".join(cells)[:80]
    except Exception:
        pass
    return ""


def _is_action_table(el, header):
    try:
        cells = [c.strip().lower() for c in (header or "").split("|")]
        if any(any(h in c for h in ACTION_COLUMN_HINTS) for c in cells):
            return True
        th = el.query_selector_all("thead th")
        for t in th:
            txt = (t.inner_text() or "").strip().lower()
            if any(h in txt for h in ACTION_COLUMN_HINTS):
                return True
        return False
    except Exception:
        return False


def _row_actions(el):
    """If the table has an action column, collect the distinct action labels."""
    if not _has_action_cells(el):
        return []
    seen = []
    for btn in el.query_selector_all(BTN_CSS):
        try:
            label = element_label(btn)
            if label and label not in seen and not _is_logout(label):
                seen.append(label)
        except Exception:
            continue
    for link in el.query_selector_all("a[href]"):
        try:
            label = element_label(link)
            if _is_action_anchor(label, link) and label not in seen and not _is_logout(label):
                seen.append(label)
        except Exception:
            continue
    return seen


def _has_action_cells(el):
    try:
        for btn in el.query_selector_all(BTN_CSS):
            if btn.is_visible():
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Full page scan
# ---------------------------------------------------------------------------

def scan_page(page):
    """Complete inventory of the current page. Returns list of action dicts."""
    actions = []
    actions += scan_buttons(page)
    actions += scan_search_fields(page)
    actions += scan_forms(page)
    for t in scan_tables(page):
        if t["action_type"] == "table" and t.get("action_column"):
            actions.append({
                "action_type": "table",
                "label": t["label"],
                "table_label": t["label"],
                "header": t.get("header", ""),
                "row_actions": t.get("row_actions", []),
                "x": t["x"], "y": t["y"], "el_index": t["el_index"],
            })
        elif t["action_type"] == "table":
            actions.append({
                "action_type": "table",
                "label": t["label"],
                "header": t.get("header", ""),
                "row_actions": [],
                "x": t["x"], "y": t["y"], "el_index": t["el_index"],
            })
    return actions


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------

def find_links(page, base_netloc, start_url):
    links = []
    try:
        raw = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({href: e.href, text: (e.innerText||'').trim()}))")
    except Exception:
        raw = []
    base_clean = _clean_url(start_url)
    for l in raw:
        href = l.get("href") or ""
        text = (l.get("text") or "").lower()
        if not href.startswith("http"):
            continue
        if not _is_same_domain(href, base_netloc):
            continue
        parsed = urlparse(href)
        if parsed.scheme in ("mailto", "tel", "javascript"):
            continue
        if _lower(href.split("?")[0]).endswith(FILE_EXT_SKIP):
            continue
        if any(h in _lower(href) for h in LOGOUT_HINTS) or any(h in text for h in LOGOUT_HINTS):
            continue
        if _clean_url(href) == base_clean:
            continue
        # Skip row-level / resource/<id>/... URLs to avoid crawling each
        # record's action pages as separate modules (section 16 - avoid loops).
        if _has_numeric_id_segment(parsed.path):
            continue
        links.append(href)
    return links


def _has_numeric_id_segment(path):
    segments = [s for s in path.split("/") if s]
    return any(seg.isdigit() for seg in segments)


def explore(session, project, on_progress=None, is_cancelled=None):
    """Crawl the app top-down and return (pages, actions).

    project: dict-ish with url/email/password_enc/auth_type/environment/comments
    """
    from urllib.parse import urlparse
    from .. import security

    page = session.page
    start_url = project["url"]
    email = project.get("email", "")
    password = security.decrypt_value(project.get("password_enc", ""))
    auth_type = project.get("auth_type", "simple")
    base_netloc = urlparse(start_url).netloc

    found = {}
    all_actions = []
    to_visit = [start_url]
    visited = set()

    def cancelled():
        return bool(is_cancelled and is_cancelled())

    try:
        session.goto(start_url)
        settle(page)
        if has_login_form(page):
            try_login(page, email, password)
            settle(page)
    except Exception as e:
        raise RuntimeError(f"Exploration - impossible de charger l'URL: {e}")

    while to_visit and len(visited) < setting_int("max_pages", env.MAX_PAGES):
        if cancelled():
            break
        current = to_visit.pop(0)
        if current in visited:
            continue
        try:
            resp = session.goto(current, timeout=20000)
            settle(page)
            status = resp.status if resp else 0
        except Exception:
            visited.add(current)
            continue

        visited.add(current)
        name = _page_title(page, current)
        found[current] = {"name": name, "status": status}

        scroll_full(page)
        for act in scan_page(page):
            act["page_url"] = current
            act["page_name"] = name
            all_actions.append(act)

        if on_progress:
            on_progress(len(found))

        for href in find_links(page, base_netloc, start_url):
            if href in visited or href in found or href in to_visit:
                continue
            to_visit.append(href)

    return found, all_actions
