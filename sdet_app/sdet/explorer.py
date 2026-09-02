"""Explorer: walks the application like a QA analyst (top-down), inventories
the real interactive elements (buttons, forms, searches, tables, actions
columns) and records a stable cartography.

Button naming priority (spec section 14):
  1. visible text  2. aria-label  3. title  4. value  5. icon  6. fallback

Interactive elements are ordered by visual position: Y then X (section 12).
"""
from urllib.parse import urlparse, urlunparse
import re
import json

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

# Utility / chrome links that are NOT real application pages: language and
# theme switchers, feeds, … Do not inventory them as modules to test (section
# 16 - avoid loops / duplicates).
_NAV_UTIL_RE = re.compile(
    r"/(?:locale|language|lang|set-language|switch-language|theme|dark-mode|"
    r"color-scheme|rss|feed|sitemap|favicon)(?:/|$)",
    re.IGNORECASE)

# "img56a.jpg (200×200)" - an <h1> reduced to an image alt must not become
# the page name; we then fall back to the document title.
_IMG_ALT_RE = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|bmp|ico|avif)\b",
                         re.IGNORECASE)

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
    """Scroll the entire page slowly from top to bottom to trigger lazy-loaded
    content (infinite scroll, deferred JS widgets, etc.), then scroll back
    to the top so the subsequent scan_page sees everything in DOM order.
    """
    try:
        page.evaluate("""async () => {
            await new Promise(resolve => {
                let total = 0;
                const step = () => {
                    window.scrollBy(0, 400);
                    total += 400;
                    const max = document.body.scrollHeight;
                    if (total >= max || total > 60000) { window.scrollTo(0,0); resolve(); }
                    else setTimeout(step, 200);
                };
                step();
            });
        }""")
        # Wait for any lazy-loaded content triggered by the scroll
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        page.wait_for_timeout(800)
    except Exception:
        pass


def _page_title(page, url):
    try:
        h1 = page.query_selector("h1")
        if h1 and h1.inner_text().strip():
            t = " ".join(h1.inner_text().split())[:80]
            if not _IMG_ALT_RE.search(t):
                return t
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

    Every physical button is kept (no de-duplication by label): two rows with
    an identical 'Modifier' action both appear, each data carries `_kept_idx`
    pointing at the raw DOM index so the runner can re-resolve its exact
    element handle (see scan_buttons_handles).

    All per-element attribute/visibility reads are batched into a single
    page-level JS evaluation, so scanning hundreds of elements stays fast.
    """
    found, _ = scan_buttons_indexed(page)
    return found


# Selector used by _json_interactive (keep in sync!) so that walking every
# button can resolve each entry's element handle by DOM index. The JS
# _json_interactive builds its selector from this SAME string to guarantee the
# `_kept_idx` DOM index aligns with the handle resolution below.
_CLICKABLE_SEL = (
    'button, [role="button"], '
    'input[type="submit"], input[type="button"], input[type="image"], '
    'a[role="menuitem"], a.btn, a[class*="btn"], a[class*="button"], '
    'a[class*="action"], a[data-action], a[aria-haspopup], a[onclick], a[href], '
    'div[class*="btn"], span[class*="btn"], li[class*="btn"], '
    'div[class*="action"], span[class*="action"], li[class*="action"], '
    'div[class*="icon"], span[class*="icon"], i[class*="icon"], '
    '[onclick], [data-action]'
)


def _scan_raw(page):
    """Run the batched scan, return (data list, kept dom-indices) in DOM order."""
    res = _json_interactive(page) or {}
    return res.get("out") or [], res.get("kept") or []


def scan_buttons_indexed(page):
    """Return (found, raw) where `found` is the button inventory and `raw` the
    parallel list of raw data dicts (same length & order as `found`).

    Each found dict carries `_kept_idx` = its original DOM index inside the
    `_CLICKABLE_SEL` result, so the runner can resolve the exact element.
    """
    raw, kept = _scan_raw(page)
    if not raw:
        return [], []

    results = []
    for d, k in zip(raw, kept):
        try:
            label = _label_from_data(d)
        except Exception:
            continue
        if not label:
            continue
        if _is_logout(label):
            continue
        if d.get("is_a"):
            if label.startswith("Bouton #"):
                continue
            if not d.get("btn_like") and not _is_action_anchor_data(label, d):
                continue
        else:
            # Non-anchor elements (div/span/li/td/...) are only real buttons if
            # they carry a strong click signal (role=button, onclick, data-action
            # or an explicit btn/button class token). This avoids turning
            # decorative text / group wrappers into fake buttons.
            if not d.get("click_signal"):
                continue
        results.append((label, d, k))

    results.sort(key=lambda t: 0 if t[1].get("visible") else 1)

    found = []
    kept_raw = []
    for label, d, k in results:
        if d.get("is_a") and not d.get("visible") and not d.get("signature"):
            continue
        x, y = d.get("x", 0), d.get("y", 0)
        found.append({
            "action_type": "button",
            "label": label,
            "x": x,
            "y": y,
            "href": d.get("href") or "",
            "_kept_idx": k,
        })
        kept_raw.append(d)
    order = sorted(range(len(found)), key=lambda i: (found[i]["y"], found[i]["x"]))
    found = [found[i] for i in order]
    kept_raw = [kept_raw[i] for i in order]

    cap = max(0, setting_int("max_buttons", 1000))
    if len(found) > cap:
        verbs = ("modifier", "supprimer", "ajouter", "voir", "desactiver",
                 "activer", "exporter", "importer", "visualiser", "editer")
        kept = [a for a in found if any(v in (a.get("label") or "").lower() for v in verbs)]
        kept_set = {id(a) for a in kept}
        rest = [a for a in found if id(a) not in kept_set]
        found = (kept + rest)[:max(cap, len(kept))]
        found = found[:cap]
    for i, a in enumerate(found):
        a["el_index"] = i
    return found, kept_raw


def scan_buttons_handles(page):
    """Return (found, handles): the button inventory plus the live Playwright
    element handle for each physical button (resolved by DOM index), so the
    runner can click every button precisely without label ambiguity."""
    found, _ = scan_buttons_indexed(page)
    handles = []
    try:
        all_handles = page.query_selector_all(_CLICKABLE_SEL)
        vis = page.evaluate("""() => {
            const out = [];
            const sel = _CLICKABLE_SEL_;
            const els = document.querySelectorAll(sel);
            for (const e of els) {
                const st = getComputedStyle(e);
                const r = e.getBoundingClientRect();
                out.push(!(st.display === 'none' || st.visibility === 'hidden' ||
                           e.hasAttribute('hidden') || e.disabled === true) &&
                          (r.height > 0 || r.width > 0));
            }
            return out;
        }""".replace("_CLICKABLE_SEL_", json.dumps(_CLICKABLE_SEL)))
        for a in found:
            k = a.get("_kept_idx")
            if k is None or not (0 <= k < len(all_handles)):
                handles.append(None)
                continue
            try:
                if k < len(vis) and vis[k]:
                    handles.append(all_handles[k])
                else:
                    handles.append(None)
            except Exception:
                handles.append(None)
    except Exception:
        handles = [None] * len(found)
    return found, handles





def _json_interactive(page):
    """Batch-read every interactive element's attributes in one JS pass.

    Returns a list of dicts (no Playwright handles kept) so the Python side can
    run its classification without per-element RPC round-trips.
    """
    js = r"""
    () => {
        const sel = _CLICKABLE_SEL_;
        const els = Array.from(document.querySelectorAll(sel));
        const out = [];
        const kept = [];
        for (let ix = 0; ix < els.length; ix++) {
            const e = els[ix];
            // --- Skip elements inside navigation containers (sidebar, navbar,
            // top-bar, menu, header, footer).  These are global chrome of the
            // target app and must NOT appear as page-specific actions.
            let isInNav = false;
            let navAnc = e.parentElement;
            while (navAnc && !isInNav) {
                const at = navAnc.tagName.toLowerCase();
                // Body/html only carry theme/layout classes (e.g. AdminLTE
                // 'sidebar-mini layout-navbar-fixed') — they must NOT
                // disqualify the whole page.
                if (at === 'body' || at === 'html') break;
                const acls = (navAnc.getAttribute && navAnc.getAttribute('class') || '').toLowerCase();
                const arole = navAnc.getAttribute && navAnc.getAttribute('role');
                if (at === 'nav' || at === 'aside' ||
                    arole === 'navigation' ||
                    /(^|[\s_-])(sidebar|side-bar|side-nav|sidenav|navbar|nav-bar|topbar|top-bar|top-nav|main-nav|primary-nav|menu-bar|app-nav|main-header|main-footer|site-header|site-footer|app-header|app-footer)([\s_-]|$)/.test(acls)) {
                    isInNav = true;
                }
                navAnc = navAnc.parentElement;
            }
            if (isInNav) continue;

            // Skip elements nested inside another already-selectable clickable
            // control (e.g. <a><i class="fa fa-eye"></i></a>): the icon glyph
            // is not itself a button, only the anchor is. Group wrappers such
            // as .btn-group / .btn-toolbar are NOT clickable, so they don't
            // disqualify the real buttons they contain.
            let anc = e.parentElement;
            let inClickable = false;
            while (anc && !inClickable) {
                const at = anc.tagName.toLowerCase();
                const acls = (anc.getAttribute && anc.getAttribute('class') || '').toLowerCase();
                const arole = anc.getAttribute && anc.getAttribute('role');
                const isGroup = /(^|[^a-z])(btn-group|btn-toolbar|input-group)([^a-z]|$)/.test(acls);
                if (at === 'button' || at === 'a' || arole === 'button' ||
                    (!isGroup && /(^|[^a-z])(btn|action|button)([^a-z]|$)/.test(acls))) {
                    inClickable = true;
                }
                anc = anc.parentElement;
            }
            if (inClickable && e.tagName.toLowerCase() !== 'a') continue;
            const isA = e.tagName.toLowerCase() === 'a';
            const cls = (e.getAttribute('class') || '').toLowerCase();
            const onclick = e.getAttribute('onclick') || '';
            const data_action = e.getAttribute('data-action') || '';
            const aria = e.getAttribute('aria-label') || '';
            const title = e.getAttribute('title') || '';
            const href = (e.getAttribute('href') || '').toLowerCase();
            // Plain <a href> without any action signal (no btn/action class,
            // no script, no accessible label, no action-looking URL) is pure
            // navigation -> the crawl already discovers it via find_links;
            // skip it here to avoid the expensive innerText/rect work on
            // link-heavy pages.
            // Collect icon classes from the element and its descendants (e.g.
            // <a><i class="fa fa-pencil"></i></a>) so icon-only actions are
            // still recognised even when the anchor itself has no action class.
            let icls = cls;
            for (let c of (e.children || [])) {
              const ccl = (c.getAttribute && c.getAttribute('class') || '') + ' ' +
                          (c.getAttribute && c.getAttribute('data-icon') || '');
              if (ccl.trim()) icls += ' ' + ccl;
            }
            const hasIconClass = /(^|[^a-z])(icon|fa|fas|far|fab|bi|material-icons|glyphicon)([^a-z]|$)/.test(icls);
            const isBtnLike = !!(
              /(^|[^a-z])(btn|action|button|icon)([^a-z]|$)/.test(cls) ||
              onclick || data_action || aria || title || hasIconClass ||
              /(create|add|new|edit|update|delete|remove|export|import|save|view|show|send)/.test(href)
            ) || !isA;
            if (isA && !isBtnLike) continue;
            const vis = !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
            const btnLike = !!(e.matches('a.btn, a[class*="btn"], a[class*="button"], a[class*="action"], a[data-action], a[aria-haspopup], a[onclick]'));
            // A strong "this is a real clickable control" signal: a native
            // control, an explicit button role, an explicit script/action hook,
            // or a class that means button/action as a whole token (NOT a group
            // wrapper like btn-group / btn-toolbar / dropdown, which must not
            // turn every div into a fake "button").
            const isNative = e.tagName.toLowerCase() === 'button' ||
              /^input$/i.test(e.tagName);
            const hasRoleBtn = e.getAttribute('role') === 'button';
            // A class means "this is a button" when `btn` appears as its own
            // token (Bootstrap `btn btn-primary`) or as a prefix (`btn-primary`),
            // but NOT when it is only part of a group/toolbar/menu wrapper
            // (btn-group, btn-toolbar, btn-block ...) such wrappers must not
            // turn every child div into a fake button.
            const isGroupCls = /(^|[\s_-])btn-(group|toolbar|block|menu)([\s_-]|$)/.test(cls);
            const clsBtnToken = !isGroupCls && (
              /(^|[\s_-])btn([\s_-]|$)/.test(cls) ||
              /(^|[\s_-])btn-[a-z]/.test(cls) ||
              /(^|[\s_-])(button|btn)([\s_-]|$)/.test(cls) ||
              /(^|[\s_-])btn-icon([\s_-]|$)/.test(cls)
            );
            const clickSignal = !!(isNative || hasRoleBtn || onclick || data_action ||
                                   (isA && btnLike) || (isA && hasIconClass) ||
                                   (!isNative && !hasRoleBtn && !onclick && !data_action && clsBtnToken));
            const el = {
              tag: isA ? 'a' : e.tagName.toLowerCase(),
              is_a: isA,
              btn_like: btnLike,
              click_signal: clickSignal,
              href: e.getAttribute('href') || '',
              cls: cls,
              icls: icls,
              text: (e.innerText || '').trim(),
              aria: aria,
              title: title,
              value: (e.getAttribute('value') || '').trim(),
              onclick: onclick,
              data_action: data_action,
              data_tooltip: e.getAttribute('data-tooltip') || e.getAttribute('data-tip') || e.getAttribute('data-original-title') || '',
              visible: vis,
            };
            el.signature = !el.is_a || vis || isBtnLike || hasIconClass;
            const r = e.getBoundingClientRect();
            el.x = Math.round(r.left + window.scrollX + r.width / 2);
            el.y = Math.round(r.top + window.scrollY + r.height / 2);
            out.push(el);
            kept.push(ix);
        }
        return { out: out, kept: kept };
    }
    """
    try:
        return page.evaluate(js.replace("_CLICKABLE_SEL_", json.dumps(_CLICKABLE_SEL)))
    except Exception:
        return []


def _icon_class_from_data(d):
    """Guess a CRUD label from icon-font/utility CSS classes (reuses the map).

    Reads both the element's own classes and those of its descendants (e.g.
    <a><i class="fa fa-pencil"></i></a>) so icon-only actions are named.
    """
    n = ((d.get("cls") or "") + " " + (d.get("icls") or "")).lower()
    n = n.replace("-", " ").replace("_", " ").replace("/", " ")
    n = n.replace("\\", " ").replace(".", " ")
    for rx, lab in _ICON_RE:
        if rx.search(n):
            return lab
    return ""


def _label_from_data(d):
    """Reconstruct the human label from batched data (mirrors element_label)."""
    text = (d.get("text") or "").strip()
    if text and not _only_icon(text):
        return " ".join(text.split())[:80]
    aria = (d.get("aria") or "").strip()
    if aria:
        return aria[:80]
    title = (d.get("title") or "").strip()
    if title:
        return title[:80]
    value = (d.get("value") or "").strip()
    if value:
        return value[:80]
    if text and _only_icon(text):
        lab = icon_label(text)
        if lab:
            return lab
    tooltip = (d.get("data_tooltip") or "").strip()
    if tooltip:
        return tooltip[:80]
    lab = _icon_class_from_data(d)
    if lab:
        return lab
    return ""


def _is_action_anchor_data(label, d):
    """Decide (from batched data) whether a plain <a href> acts as an action."""
    n = normalize(label)
    if any(h in n for h in _ACTION_HINTS):
        return True
    href = d.get("href", "").lower()
    if any(h in href for h in _HREF_HINTS):
        return True
    if d.get("onclick"):
        return True
    cls = (d.get("cls", "") + " " + (d.get("icls") or "")).lower()
    if any(k in cls for k in ("btn", "action", "button", "dropdown", "icon", "icon-btn")) and \
            (d.get("aria") or d.get("title") or d.get("text") or _icon_class_from_data(d)):
        return True
    return False


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
    candidates = []
    try:
        candidates += list(el.query_selector_all(BTN_CSS))
    except Exception:
        pass
    try:
        candidates += list(el.query_selector_all("a[href]"))
    except Exception:
        pass
    if not _has_action_cells(candidates):
        return []
    seen = []
    for btn in candidates:
        try:
            label = element_label(btn)
            if label and label not in seen and not _is_logout(label):
                seen.append(label)
        except Exception:
            continue
    return seen


def _has_action_cells(candidates=None):
    """Does the page/table contain real clickable per-row actions?

    Accepts a pre-collected candidate list (native buttons + action anchors).
    When a native <button> is present it always counts; otherwise we accept a
    visible element that looks like an action control (btn/action/button/icon
    class, an accessible title/aria-label, or a scripted onclick) so icon-only
    '<a class="btn">' row actions are no longer missed.
    """
    if not candidates:
        return False
    for btn in candidates:
        try:
            if not btn.is_visible():
                continue
            cls = _lower(_attr(btn, "class") or "")
            if any(k in cls for k in ("btn", "action", "button", "icon", "dropdown")):
                return True
            if _attr(btn, "aria-label") or _attr(btn, "title"):
                return True
            if _attr(btn, "onclick"):
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Full page scan
# ---------------------------------------------------------------------------

def scan_page(page):
    """Complete inventory of the current page. Returns list of action dicts.

    The DOM scan runs first and is authoritative — plays the driver role.
    """
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
    seen = set()
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
        if _NAV_UTIL_RE.search(parsed.path):
            continue
        if any(h in _lower(href) for h in LOGOUT_HINTS) or any(h in text for h in LOGOUT_HINTS):
            continue
        # Normalize before de-duplication: strip query string and fragment so
        # pagination (?page=2), language (?lang=fr), anchors (#...) etc. are not
        # re-visited as if they were brand new pages (section 16 - avoid loops).
        clean = _clean_url(href)
        if clean == base_clean or clean in seen:
            continue
        # Skip row-level /resource/<id>/... URLs to avoid crawling each
        # record's action pages as separate modules (section 16 - avoid loops).
        if _has_numeric_id_segment(parsed.path):
            continue
        seen.add(clean)
        links.append(clean)
    return links


def _has_numeric_id_segment(path):
    segments = [s for s in path.split("/") if s]
    return any(seg.isdigit() for seg in segments)


def _strip_cross_module_navigation(all_actions, page_urls, base_url):
    """Remove actions that are pure navigation links to OTHER modules.

    The crawl records every clickable element on a page, including the
    persistent global menu (sidebar / app switcher) which links to other
    modules. Those links point to a URL that is itself one of the visited
    pages, but a different one from the action's own page. Keeping them makes
    every module's action list look "mixed" with all the other modules'
    entries, so we drop them here. Actions that stay on the same page, or that
    point to a record (contains a numeric id segment), or that are real buttons
    are preserved.
    """
    from urllib.parse import urljoin

    cleaned_pages = {_clean_url(u) for u in page_urls if u}
    out = []
    for act in all_actions:
        href = act.get("href") or ""
        if href:
            try:
                target = _clean_url(urljoin(base_url, href))
            except Exception:
                target = ""
            current = _clean_url(act.get("page_url") or "")
            if target and target in cleaned_pages and target != current:
                # Pure navigation link to another module -> skip.
                continue
        out.append(act)
    return out


def explore(session, project, on_progress=None, is_cancelled=None):
    """Crawl the app top-down and return (pages, actions).

    Behaviour (spec sections 10-16):
      - Arrive on a page, scan EVERYTHING from top to bottom in one pass:
        buttons, search fields, forms, tables.
      - Once the scan is complete, discover navigation links for future pages.
      - NEVER revisit a page that was already scanned.
      - Order: top-to-bottom (Y then X) so the cartography is faithful.

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
    visited = set()   # pages we have fully scanned
    seen = {_clean_url(start_url)}  # pages queued or already visited

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
        key = _clean_url(current)
        if key in visited:
            continue

        # Mark as visited IMMEDIATELY so no other thread/path can re-queue it.
        visited.add(key)

        try:
            resp = session.goto(current, timeout=20000)
            settle(page)
            status = resp.status if resp else 0
        except Exception:
            continue

        name = _page_title(page, current)
        found[key] = {"name": name, "status": status}

        # --- COMPREHENSIVE SCAN in one pass, top to bottom ---
        # 1. Scroll slowly to trigger lazy-loaded content
        scroll_full(page)
        # 2. Scan every interactive element: buttons, search, forms, tables
        for act in scan_page(page):
            act["page_url"] = key
            act["page_name"] = name
            all_actions.append(act)

        if on_progress:
            on_progress(len(found))

        # --- DISCOVER LINKS for future pages (never re-scan current page) ---
        for href in find_links(page, base_netloc, start_url):
            k = _clean_url(href)
            if k in visited or k in seen:
                continue
            seen.add(k)
            to_visit.append(href)

    all_actions = _strip_cross_module_navigation(all_actions, list(found.keys()), start_url)
    return found, all_actions
